"""Accounts: signup, login, logout and the signed-in member.

Sessions are opaque random tokens in an httpOnly cookie; the database keeps only their SHA-256, so
a leaked database can't be used to sign in. Passwords are hashed with argon2id. The browser talks
to this API through the Next.js server (same origin), so the cookie is first-party and SameSite=Lax;
FastAPI only parses JSON bodies sent as application/json, which a cross-site form can't send.
"""

import hashlib
import os
import re
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from typing import Annotated

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import delete, select

from api import cache
from db import AgentProfile, BusinessHours, Membership, Organization, User, UserSession, session_scope

COOKIE_NAME = "tb_session"
SESSION_TTL = timedelta(days=30)
# Secure cookies need HTTPS; local development runs on plain http://localhost.
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "false").strip().lower() in ("1", "true", "yes", "on")
# New businesses start open Monday to Saturday, 10:00-20:00; owners change it in the dashboard.
DEFAULT_HOURS = [(day, time(10), time(20)) for day in range(6)]

_hasher = PasswordHasher()
# Verifying against this when the email is unknown keeps login timing the same either way.
_DUMMY_HASH = _hasher.hash("talkbalt-timing-equalizer")
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

router = APIRouter(prefix="/api/auth", tags=["auth"])


class SignupRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    email: str = Field(max_length=254)
    password: str = Field(min_length=8, max_length=200)
    business_name: str = Field(min_length=1, max_length=160)

    @field_validator("email")
    @classmethod
    def valid_email(cls, value: str) -> str:
        value = value.strip().lower()
        if not _EMAIL.match(value):
            raise ValueError("Enter a valid email address")
        return value


class LoginRequest(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(max_length=200)


class MeResponse(BaseModel):
    user_id: uuid.UUID
    name: str
    email: str
    org_id: uuid.UUID
    org_name: str
    role: str
    is_admin: bool = False  # Talkbalt operator: may see costs, tokens and latency


@dataclass(frozen=True)
class Member:
    """The signed-in user and the organization their requests act on."""

    user_id: uuid.UUID
    org_id: uuid.UUID
    role: str


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _slug(name: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:60] or "business"
    return f"{base}-{secrets.token_hex(3)}"


async def _start_session(response: Response, user_id: uuid.UUID) -> None:
    token = secrets.token_urlsafe(32)
    async with session_scope() as db:
        db.add(UserSession(user_id=user_id, token_hash=_token_hash(token), expires_at=datetime.now(UTC) + SESSION_TTL))
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=int(SESSION_TTL.total_seconds()),
        httponly=True,
        secure=COOKIE_SECURE,
        samesite="lax",
        path="/",
    )


async def current_member(tb_session: Annotated[str | None, Cookie()] = None) -> Member:
    if not tb_session:
        raise HTTPException(status_code=401, detail="Sign in to continue")
    async with session_scope() as db:
        row = (
            await db.execute(
                select(UserSession.user_id, Membership.org_id, Membership.role)
                .join(Membership, Membership.user_id == UserSession.user_id)
                .where(UserSession.token_hash == _token_hash(tb_session), UserSession.expires_at > datetime.now(UTC))
                .limit(1)
            )
        ).first()
    if row is None:
        raise HTTPException(status_code=401, detail="Your session has ended, sign in again")
    return Member(user_id=row.user_id, org_id=row.org_id, role=row.role)


CurrentMember = Annotated[Member, Depends(current_member)]


def is_platform_admin(email: str | None) -> bool:
    """Talkbalt's own operators (PLATFORM_ADMIN_EMAILS, comma separated). Only they see costs,
    tokens and latency; business owners see calls and bookings."""
    allowed = {e.strip().lower() for e in os.getenv("PLATFORM_ADMIN_EMAILS", "").split(",") if e.strip()}
    return bool(email) and email.lower() in allowed


async def platform_admin(member: CurrentMember) -> Member:
    async with session_scope() as db:
        email = await db.scalar(select(User.email).where(User.id == member.user_id))
    if not is_platform_admin(email):
        raise HTTPException(status_code=403, detail="Only Talkbalt admins can see this")
    return member


PlatformAdmin = Annotated[Member, Depends(platform_admin)]


@router.post("/signup", status_code=201)
async def signup(body: SignupRequest, response: Response) -> MeResponse:
    async with session_scope() as db:
        if await db.scalar(select(User.id).where(User.email == body.email)):
            raise HTTPException(status_code=409, detail="An account with this email already exists")
        user = User(email=body.email, password_hash=_hasher.hash(body.password), name=body.name.strip())
        org = Organization(name=body.business_name.strip(), slug=_slug(body.business_name))
        db.add_all([user, org])
        await db.flush()
        db.add(Membership(org_id=org.id, user_id=user.id, role="owner"))
    # The business's own rows are written through its tenant scope, as everywhere else.
    async with session_scope(org.id) as db:
        db.add(AgentProfile(org_id=org.id, business_name=org.name))
        db.add_all(BusinessHours(org_id=org.id, weekday=d, opens=o, closes=c) for d, o, c in DEFAULT_HOURS)
    await _start_session(response, user.id)
    return MeResponse(user_id=user.id, name=user.name, email=user.email, org_id=org.id, org_name=org.name, role="owner")


LOGIN_LIMIT = (10, 900)  # attempts per email, and per address, per 15 minutes


@router.post("/login")
async def login(body: LoginRequest, response: Response, request: Request) -> MeResponse:
    ip = request.headers.get("x-forwarded-for", "").split(",")[0].strip() or (request.client.host if request.client else "")
    email = body.email.strip().lower()
    if not (await cache.allow(f"login:email:{email}", *LOGIN_LIMIT) and await cache.allow(f"login:ip:{ip}", *LOGIN_LIMIT)):
        raise HTTPException(status_code=429, detail="Too many sign-in attempts. Wait a few minutes and try again.")
    async with session_scope() as db:
        row = (
            await db.execute(
                select(User, Organization, Membership.role)
                .join(Membership, Membership.user_id == User.id)
                .join(Organization, Organization.id == Membership.org_id)
                .where(User.email == body.email.strip().lower())
                .limit(1)
            )
        ).first()
    try:
        _hasher.verify(row.User.password_hash if row else _DUMMY_HASH, body.password)
    except (VerificationError, InvalidHashError):
        row = None
    if row is None:
        raise HTTPException(status_code=401, detail="Wrong email or password")
    await _start_session(response, row.User.id)
    return MeResponse(
        user_id=row.User.id, name=row.User.name, email=row.User.email,
        org_id=row.Organization.id, org_name=row.Organization.name, role=row.role,
        is_admin=is_platform_admin(row.User.email),
    )


@router.post("/logout", status_code=204)
async def logout(response: Response, tb_session: Annotated[str | None, Cookie()] = None) -> None:
    if tb_session:
        async with session_scope() as db:
            await db.execute(delete(UserSession).where(UserSession.token_hash == _token_hash(tb_session)))
    response.delete_cookie(COOKIE_NAME, path="/")


@router.get("/me")
async def me(member: CurrentMember) -> MeResponse:
    async with session_scope() as db:
        user = await db.get(User, member.user_id)
        org = await db.get(Organization, member.org_id)
    assert user is not None and org is not None
    return MeResponse(user_id=user.id, name=user.name, email=user.email, org_id=org.id, org_name=org.name, role=member.role,
                      is_admin=is_platform_admin(user.email))
