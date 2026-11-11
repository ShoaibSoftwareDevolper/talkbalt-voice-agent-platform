"""The business's website widget and logo: how the widget button looks, and the logo shown on the
public page and in the widget.

Logos live in Cloudinary, one folder per business (talkbalt/<slug>/), always as `logo` so a new
upload replaces the old one. The browser uploads straight to Cloudinary with a signature made here,
so the API secret never leaves the server and the file never passes through this API.
Configure with CLOUDINARY_CLOUD_NAME, CLOUDINARY_API_KEY and CLOUDINARY_API_SECRET.
"""

import hashlib
import os
import re
import time

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator

from api import cache
from api.auth import CurrentMember
from db import AgentProfile, Organization, session_scope

router = APIRouter(prefix="/api/org", tags=["widget"])

UPLOAD_LIMIT = (10, 3600)  # logo uploads per business per hour
LOGO_FORMATS = "png,jpg,jpeg,webp"  # no SVG: it can carry scripts


class WidgetSettings(BaseModel):
    label: str = Field(default="Start your call", min_length=1, max_length=40)
    color: str = Field(default="#4b46d6", pattern=r"^#[0-9a-fA-F]{6}$")
    position: str = Field(default="right", pattern=r"^(right|left)$")
    logo_url: str | None = Field(default=None, max_length=500)

    @field_validator("label")
    @classmethod
    def trimmed(cls, value: str) -> str:
        return value.strip() or "Start your call"


class LogoSignature(BaseModel):
    cloud_name: str
    api_key: str
    timestamp: int
    signature: str
    public_id: str
    asset_folder: str
    overwrite: str
    invalidate: str
    allowed_formats: str


def _cloudinary() -> tuple[str, str, str] | None:
    values = tuple(os.getenv(k, "").strip() for k in ("CLOUDINARY_CLOUD_NAME", "CLOUDINARY_API_KEY", "CLOUDINARY_API_SECRET"))
    return values if all(values) else None  # type: ignore[return-value]


def _folder(slug: str) -> str:
    return f"talkbalt/{slug}"


def sign(params: dict[str, str | int], secret: str) -> str:
    """Cloudinary's upload signature: the params sorted by name as name=value joined with &, the API
    secret appended, hashed with SHA-1."""
    payload = "&".join(f"{k}={params[k]}" for k in sorted(params)) + secret
    return hashlib.sha1(payload.encode()).hexdigest()


def _logo_url_ok(url: str, cloud_name: str, slug: str) -> bool:
    """Only a logo this business uploaded to its own folder in our Cloudinary account."""
    pattern = rf"^https://res\.cloudinary\.com/{re.escape(cloud_name)}/image/upload/(?:[^/]+/)*{re.escape(_folder(slug))}/logo\.(png|jpe?g|webp)$"
    return re.match(pattern, url) is not None


async def _load(member_org) -> tuple[AgentProfile, Organization]:
    async with session_scope(member_org) as db:
        profile = await db.get(AgentProfile, member_org)
        org = await db.get(Organization, member_org)
    assert profile is not None and org is not None
    return profile, org


@router.get("/widget")
async def get_widget(member: CurrentMember) -> WidgetSettings:
    profile, _ = await _load(member.org_id)
    return WidgetSettings(label=profile.widget_label, color=profile.widget_color, position=profile.widget_position,
                          logo_url=profile.logo_url)


@router.put("/widget")
async def put_widget(body: WidgetSettings, member: CurrentMember) -> WidgetSettings:
    _, org = await _load(member.org_id)
    if body.logo_url:
        config = _cloudinary()
        if config is None or not _logo_url_ok(body.logo_url, config[0], org.slug):
            raise HTTPException(status_code=422, detail="That logo address isn't one uploaded from this dashboard")
    async with session_scope(member.org_id) as db:
        profile = await db.get(AgentProfile, member.org_id)
        assert profile is not None
        profile.widget_label, profile.widget_color, profile.widget_position = body.label, body.color.lower(), body.position
        profile.logo_url = body.logo_url
    return body


@router.post("/logo/signature")
async def logo_signature(member: CurrentMember) -> LogoSignature:
    config = _cloudinary()
    if config is None:
        raise HTTPException(status_code=503, detail="Logo upload isn't set up yet (Cloudinary keys are missing on the server)")
    if not await cache.allow(f"logo:{member.org_id}", *UPLOAD_LIMIT):
        raise HTTPException(status_code=429, detail="Too many logo uploads. Please try again in a while.")
    cloud_name, api_key, secret = config
    _, org = await _load(member.org_id)
    folder = _folder(org.slug)
    # asset_folder places it in the folder (dynamic folder mode); the path in public_id does the same
    # in fixed folder mode, and makes the URL say whose logo it is.
    params: dict[str, str | int] = {
        "timestamp": int(time.time()),
        "public_id": f"{folder}/logo",
        "asset_folder": folder,
        "overwrite": "true",
        "invalidate": "true",
        "allowed_formats": LOGO_FORMATS,
    }
    return LogoSignature(cloud_name=cloud_name, api_key=api_key, signature=sign(params, secret), **params)  # type: ignore[arg-type]
