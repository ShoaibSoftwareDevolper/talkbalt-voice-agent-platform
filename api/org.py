"""The signed-in business's own setup and bookings. Every query runs in the business's tenant scope
(row level security), on top of the explicit org filters."""

import json
import os
import uuid
from datetime import date, datetime, time, timedelta
from typing import Annotated, Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Query
from livekit import api as lk
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from api import cache
from api.auth import CurrentMember
from db import AgentProfile, Booking, BusinessHours, Customer, Organization, Service, session_scope

AGENT_NAME = "talkbalt-agent"
TOKEN_TTL = timedelta(minutes=10)

router = APIRouter(prefix="/api/org", tags=["business"])


# ---- Profile ---------------------------------------------------------------------------------


class Faq(BaseModel):
    q: str = Field(min_length=1, max_length=300)
    a: str = Field(min_length=1, max_length=1000)


class Profile(BaseModel):
    business_name: str = Field(min_length=1, max_length=160)
    greeting: str | None = Field(default=None, max_length=300)
    about: str | None = Field(default=None, max_length=2000)
    timezone: str = Field(default="Asia/Karachi", max_length=64)
    location_type: Literal["in_person", "remote", "both"] = "in_person"
    address: str | None = Field(default=None, max_length=300)
    city: str | None = Field(default=None, max_length=120)
    country: str | None = Field(default=None, max_length=80)
    contact_email: str | None = Field(default=None, max_length=254)
    website: str | None = Field(default=None, max_length=300)
    faqs: list[Faq] = Field(default_factory=list, max_length=30)
    public_calls_enabled: bool = True
    slug: str | None = None  # read-only: the business's public link is /b/<slug>

    @field_validator("timezone")
    @classmethod
    def known_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except Exception as e:
            raise ValueError("Unknown time zone") from e
        return value


@router.get("/profile")
async def get_profile(member: CurrentMember) -> Profile:
    async with session_scope(member.org_id) as db:
        profile = await db.get(AgentProfile, member.org_id)
        org = await db.get(Organization, member.org_id)
    assert profile is not None and org is not None
    return Profile(
        business_name=profile.business_name, greeting=profile.greeting, about=profile.about, timezone=org.timezone,
        location_type=profile.location_type, address=profile.address, city=profile.city, country=profile.country,
        contact_email=profile.contact_email, website=profile.website, faqs=[Faq(**f) for f in profile.faqs],
        public_calls_enabled=profile.public_calls_enabled, slug=org.slug,
    )


@router.put("/profile")
async def put_profile(body: Profile, member: CurrentMember) -> Profile:
    async with session_scope(member.org_id) as db:
        profile = await db.get(AgentProfile, member.org_id)
        assert profile is not None
        for key in ("business_name", "greeting", "about", "location_type", "address", "city", "country",
                    "contact_email", "website", "public_calls_enabled"):
            setattr(profile, key, getattr(body, key))
        profile.faqs = [f.model_dump() for f in body.faqs]
    # organizations is shared (no RLS); only this member's own org row is touched.
    async with session_scope() as db:
        org = await db.get(Organization, member.org_id)
        assert org is not None
        org.name, org.timezone = body.business_name, body.timezone
        body.slug = org.slug
    await cache.invalidate_runtime(member.org_id)
    return body


# ---- Services --------------------------------------------------------------------------------


class ServiceIn(BaseModel):
    kind: Literal["service", "product"] = "service"
    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=500)
    duration_min: int = Field(default=30, ge=5, le=480)
    price_cents: int | None = Field(default=None, ge=0)
    currency: str = Field(default="PKR", min_length=3, max_length=3)
    active: bool = True


class ServiceOut(ServiceIn):
    id: uuid.UUID


def _service_out(s: Service) -> ServiceOut:
    return ServiceOut(
        id=s.id, kind=s.kind, name=s.name, description=s.description, duration_min=s.duration_min,
        price_cents=s.price_cents, currency=s.currency, active=s.active,
    )


@router.get("/services")
async def list_services(member: CurrentMember) -> list[ServiceOut]:
    async with session_scope(member.org_id) as db:
        rows = await db.scalars(select(Service).order_by(Service.sort_order, Service.name))
        return [_service_out(s) for s in rows]


@router.post("/services", status_code=201)
async def create_service(body: ServiceIn, member: CurrentMember) -> ServiceOut:
    try:
        async with session_scope(member.org_id) as db:
            service = Service(org_id=member.org_id, **body.model_dump())
            db.add(service)
    except IntegrityError as e:
        raise HTTPException(status_code=409, detail="A service with this name already exists") from e
    await cache.invalidate_runtime(member.org_id)
    return _service_out(service)


@router.put("/services/{service_id}")
async def update_service(service_id: uuid.UUID, body: ServiceIn, member: CurrentMember) -> ServiceOut:
    try:
        async with session_scope(member.org_id) as db:
            service = await db.get(Service, service_id)
            if service is None:
                raise HTTPException(status_code=404, detail="Service not found")
            for key, value in body.model_dump().items():
                setattr(service, key, value)
    except IntegrityError as e:
        raise HTTPException(status_code=409, detail="A service with this name already exists") from e
    await cache.invalidate_runtime(member.org_id)
    return _service_out(service)


@router.delete("/services/{service_id}", status_code=204)
async def delete_service(service_id: uuid.UUID, member: CurrentMember) -> None:
    async with session_scope(member.org_id) as db:
        result = await db.execute(delete(Service).where(Service.id == service_id))
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="Service not found")
    await cache.invalidate_runtime(member.org_id)


# ---- Opening hours ---------------------------------------------------------------------------


class Window(BaseModel):
    weekday: int = Field(ge=0, le=6)  # 0 = Monday
    opens: time
    closes: time

    @model_validator(mode="after")
    def closes_after_opening(self) -> "Window":
        if self.closes <= self.opens:
            raise ValueError("Closing time must be after opening time")
        return self


@router.get("/hours")
async def get_hours(member: CurrentMember) -> list[Window]:
    async with session_scope(member.org_id) as db:
        rows = await db.scalars(select(BusinessHours).order_by(BusinessHours.weekday, BusinessHours.opens))
        return [Window(weekday=h.weekday, opens=h.opens, closes=h.closes) for h in rows]


@router.put("/hours")
async def put_hours(body: list[Window], member: CurrentMember) -> list[Window]:
    """Replaces the whole week. A weekday with no window is closed."""
    by_day: dict[int, list[Window]] = {}
    for w in sorted(body, key=lambda w: (w.weekday, w.opens)):
        day = by_day.setdefault(w.weekday, [])
        if day and w.opens < day[-1].closes:
            raise HTTPException(status_code=422, detail="Opening windows on the same day overlap")
        day.append(w)
    async with session_scope(member.org_id) as db:
        await db.execute(delete(BusinessHours))
        db.add_all(BusinessHours(org_id=member.org_id, **w.model_dump()) for w in body)
    await cache.invalidate_runtime(member.org_id)
    return body


# ---- Bookings --------------------------------------------------------------------------------


class BookingOut(BaseModel):
    id: uuid.UUID
    service_name: str
    starts_at: datetime
    ends_at: datetime
    status: str
    source: str
    customer_name: str
    customer_phone: str
    call_id: uuid.UUID | None


@router.get("/bookings")
async def list_bookings(
    member: CurrentMember,
    from_: Annotated[date | None, Query(alias="from")] = None,
    to: Annotated[date | None, Query()] = None,
) -> list[BookingOut]:
    """Bookings between two days (inclusive) in the business's time zone; default: today + 30 days."""
    async with session_scope(member.org_id) as db:
        org = await db.get(Organization, member.org_id)
        assert org is not None
        tz = ZoneInfo(org.timezone)
        start_day = from_ or datetime.now(tz).date()
        end_day = to or start_day + timedelta(days=30)
        if (end_day - start_day).days > 366 or end_day < start_day:
            raise HTTPException(status_code=400, detail="Pick a range of up to one year")
        start = datetime.combine(start_day, time.min, tz)
        end = datetime.combine(end_day + timedelta(days=1), time.min, tz)
        rows = await db.execute(
            select(Booking, Customer)
            .join(Customer, Customer.id == Booking.customer_id)
            .where(Booking.starts_at >= start, Booking.starts_at < end)
            .order_by(Booking.starts_at)
        )
        return [
            BookingOut(
                id=b.id, service_name=b.service_name, starts_at=b.starts_at, ends_at=b.ends_at, status=b.status,
                source=b.source, customer_name=c.name, customer_phone=c.phone_e164, call_id=b.call_id,
            )
            for b, c in rows
        ]


@router.post("/bookings/{booking_id}/cancel")
async def cancel_booking(booking_id: uuid.UUID, member: CurrentMember) -> dict[str, str]:
    async with session_scope(member.org_id) as db:
        booking = await db.get(Booking, booking_id)
        if booking is None:
            raise HTTPException(status_code=404, detail="Booking not found")
        booking.status = "cancelled"
    return {"status": "cancelled"}


# ---- Calls from the dashboard, the public link and the widget -------------------------------


class CallToken(BaseModel):
    serverUrl: str
    participantToken: str


def issue_call_token(org_id: uuid.UUID, source: str) -> CallToken:
    """A token for a browser call with this business's agent. The agent reads the organization
    from the dispatch metadata, so it answers with this business's setup and books into its calendar."""
    server_url, key, secret = (os.getenv(k) for k in ("LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET"))
    if not (server_url and key and secret):
        raise HTTPException(status_code=500, detail="Server configuration error")
    room = f"talkbalt-{uuid.uuid4().hex}"
    metadata = json.dumps({"org_id": str(org_id), "source": source})
    token = (
        lk.AccessToken(key, secret)
        .with_identity(f"caller-{uuid.uuid4().hex[:12]}")
        .with_name("Caller")
        .with_ttl(TOKEN_TTL)
        .with_grants(lk.VideoGrants(room_join=True, room=room, can_publish=True, can_subscribe=True, can_publish_data=True))
        .with_room_config(lk.RoomConfiguration(agents=[lk.RoomAgentDispatch(agent_name=AGENT_NAME, metadata=metadata)]))
    )
    return CallToken(serverUrl=server_url, participantToken=token.to_jwt())


@router.post("/test-call", status_code=201)
async def test_call_token(member: CurrentMember) -> CallToken:
    return issue_call_token(member.org_id, "dashboard")
