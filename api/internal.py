"""Service-to-service API for the voice agent (backend-web). Never reachable from browsers: the
Next.js proxy only forwards /api/*, and every route here needs the shared INTERNAL_API_TOKEN.

The voice agent never touches this database directly. It asks here for a business's setup at the
start of a call, registers the call, books appointments, and sends the finished call report.
"""

import logging
import os
import secrets
import uuid
from datetime import datetime, timedelta
from typing import Annotated, Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError

from api import cache
from db import (
    AgentProfile,
    Booking,
    BusinessHours,
    Call,
    CallEvent,
    CallTurnMetric,
    Customer,
    Organization,
    Service,
    session_scope,
)
from pricing import estimate_call_cost

logger = logging.getLogger("talkbalt-platform")
SLOT_STEP = timedelta(minutes=30)
RECORDED = ("recording", "processing", "completed")


def require_service_token(x_internal_token: Annotated[str | None, Header()] = None) -> None:
    expected = os.getenv("INTERNAL_API_TOKEN")
    if not expected or not x_internal_token or not secrets.compare_digest(x_internal_token, expected):
        raise HTTPException(status_code=401, detail="Invalid service token")


router = APIRouter(prefix="/internal", tags=["internal"], dependencies=[Depends(require_service_token)], include_in_schema=False)


# ---- Business setup for a call ---------------------------------------------------------------


async def build_runtime(org_id: uuid.UUID) -> dict[str, Any] | None:
    async with session_scope(org_id) as db:
        org = await db.get(Organization, org_id)
        profile = await db.get(AgentProfile, org_id)
        if org is None or profile is None:
            return None
        items = (await db.scalars(select(Service).where(Service.active).order_by(Service.sort_order, Service.name))).all()
        hours = (await db.scalars(select(BusinessHours).order_by(BusinessHours.weekday, BusinessHours.opens))).all()
    return {
        "org_id": str(org_id),
        "name": profile.business_name,
        "timezone": org.timezone,
        "greeting": profile.greeting,
        "language": profile.language,
        "voice": profile.voice,
        "about": profile.about,
        "location_type": profile.location_type,
        "address": ", ".join(p for p in (profile.address, profile.city, profile.country) if p) or None,
        "website": profile.website,
        "contact_email": profile.contact_email,
        "faqs": [f for f in profile.faqs if f.get("q") and f.get("a")],
        "hours": [{"weekday": h.weekday, "opens": h.opens.isoformat(), "closes": h.closes.isoformat()} for h in hours],
        "items": [
            {"id": str(s.id), "kind": s.kind, "name": s.name, "duration_min": s.duration_min,
             "price_cents": s.price_cents, "currency": s.currency}
            for s in items
        ],
    }


@router.get("/businesses/{org_id}/runtime")
async def business_runtime(org_id: uuid.UUID) -> dict[str, Any]:
    if runtime := await cache.get_runtime(org_id):
        return runtime
    runtime = await build_runtime(org_id)
    if runtime is None:
        raise HTTPException(status_code=404, detail="Business not found")
    await cache.set_runtime(org_id, runtime)
    return runtime


# ---- Calls -----------------------------------------------------------------------------------


class CallStart(BaseModel):
    id: uuid.UUID  # chosen by the agent, so bookings made during the call can reference it
    room_name: str = Field(max_length=255)
    call_type: str = Field(max_length=16)
    llm_model: str | None = Field(default=None, max_length=128)
    org_id: uuid.UUID | None = None
    caller_number_masked: str | None = Field(default=None, max_length=16)
    recording_status: str = Field(max_length=16)
    started_at: datetime


@router.post("/calls", status_code=201)
async def start_call(body: CallStart) -> dict[str, str]:
    async with session_scope() as db:
        await db.execute(insert(Call).values(**body.model_dump()).on_conflict_do_nothing(index_elements=["id"]))
    return {"id": str(body.id)}


class CallReport(BaseModel):
    """Everything about a finished call, sent once by the agent at session end."""

    ended_at: datetime
    duration_seconds: float
    end_reason: str
    transcript: list[dict[str, Any]]
    recording_status: str
    recording_key: str | None = None
    egress_id: str | None = None
    averages: dict[str, int | None]
    totals: dict[str, float]
    turns: list[dict[str, Any]]
    events: list[dict[str, Any]]


def _with_datetimes(row: dict[str, Any], *keys: str) -> dict[str, Any]:
    """JSON carries timestamps as ISO strings; the database columns want datetimes."""
    return {k: (datetime.fromisoformat(v) if k in keys and isinstance(v, str) else v) for k, v in row.items()}


@router.post("/calls/{call_id}/report")
async def report_call(call_id: uuid.UUID, body: CallReport) -> dict[str, str]:
    """Idempotent: a report retried from the agent's outbox replaces the turns and events it wrote."""
    async with session_scope() as db:
        call = await db.get(Call, call_id)
        if call is None:
            raise HTTPException(status_code=404, detail="Call not found")
        tot = body.totals
        cost, breakdown = estimate_call_cost(
            llm_model=call.llm_model,
            prompt_tokens=int(tot.get("prompt", 0)),
            cached_tokens=int(tot.get("cached", 0)),
            completion_tokens=int(tot.get("completion", 0)),
            stt_audio_seconds=tot.get("stt_seconds", 0.0),
            tts_characters=int(tot.get("tts_characters", 0)),
            duration_seconds=body.duration_seconds,
            call_type=call.call_type,
            recorded=body.recording_status in RECORDED,
        )
        await db.execute(
            update(Call).where(Call.id == call_id).values(
                ended_at=body.ended_at,
                duration_seconds=body.duration_seconds,
                end_reason=body.end_reason,
                turn_count=sum(1 for t in body.transcript if t.get("role") == "user"),
                transcript=body.transcript,
                recording_status=body.recording_status,
                recording_key=body.recording_key,
                egress_id=body.egress_id,
                **body.averages,
                total_prompt_tokens=int(tot.get("prompt", 0)),
                total_cached_tokens=int(tot.get("cached", 0)),
                total_completion_tokens=int(tot.get("completion", 0)),
                total_reasoning_tokens=int(tot.get("reasoning", 0)),
                llm_requests=int(tot.get("llm_requests", 0)),
                total_stt_audio_seconds=round(tot.get("stt_seconds", 0.0), 2),
                total_tts_characters=int(tot.get("tts_characters", 0)),
                total_tts_audio_seconds=round(tot.get("tts_seconds", 0.0), 2),
                cache_hit_ratio=round(tot["cached"] / tot["prompt"], 4) if tot.get("prompt") else None,
                estimated_cost_usd=cost,
                cost_breakdown=breakdown,
            )
        )
        await db.execute(CallTurnMetric.__table__.delete().where(CallTurnMetric.call_id == call_id))
        await db.execute(CallEvent.__table__.delete().where(CallEvent.call_id == call_id))
        db.add_all(CallTurnMetric(call_id=call_id, **_with_datetimes(row, "started_at", "ended_at")) for row in body.turns)
        db.add_all(CallEvent(call_id=call_id, **_with_datetimes(ev, "timestamp")) for ev in body.events)
    return {"status": "stored"}


# ---- Bookings --------------------------------------------------------------------------------


class BookingRequest(BaseModel):
    org_id: uuid.UUID
    call_id: uuid.UUID | None = None
    service_id: uuid.UUID | None = None
    service_name: str = Field(max_length=120)
    starts_at: datetime  # timezone-aware
    duration_min: int = Field(ge=5, le=480)
    customer_name: str = Field(max_length=160)
    customer_phone: str = Field(max_length=20)  # E.164
    source: str = Field(max_length=16)


class BookingResponse(BaseModel):
    status: str  # confirmed | conflict | error
    booking_id: uuid.UUID | None = None
    alternatives: list[str] = Field(default_factory=list)  # free start times (HH:MM, business time) that day


async def _free_times(org_id: uuid.UUID, start: datetime, length: timedelta, count: int = 2) -> list[str]:
    """The free start times nearest the requested one, same day, within opening hours, not past."""
    async with session_scope(org_id) as db:
        org = await db.get(Organization, org_id)
        assert org is not None
        tz = ZoneInfo(org.timezone)
        local = start.astimezone(tz)
        span = (
            await db.execute(select(BusinessHours.opens, BusinessHours.closes).where(BusinessHours.weekday == local.weekday()))
        ).first()
        if span is None:
            return []
        day_open = datetime.combine(local.date(), span.opens, tz)
        day_close = datetime.combine(local.date(), span.closes, tz)
        taken = (
            await db.execute(
                select(Booking.starts_at, Booking.ends_at).where(
                    Booking.status != "cancelled", Booking.starts_at < day_close, Booking.ends_at > day_open
                )
            )
        ).all()
    now = datetime.now(tz)
    free, slot = [], day_open
    while slot < day_close:
        if slot > now and not any(s < slot + length and e > slot for s, e in taken):
            free.append(slot)
        slot += SLOT_STEP
    nearest = sorted(free, key=lambda t: abs(t - local))[:count]
    return [t.strftime("%H:%M") for t in sorted(nearest)]


@router.post("/bookings")
async def create_booking(body: BookingRequest) -> BookingResponse:
    length = timedelta(minutes=body.duration_min)
    try:
        async with session_scope(body.org_id) as db:
            customer_id = await db.scalar(
                insert(Customer)
                .values(org_id=body.org_id, name=body.customer_name, phone_e164=body.customer_phone)
                .on_conflict_do_update(index_elements=["org_id", "phone_e164"], set_={"name": body.customer_name})
                .returning(Customer.id)
            )
            booking = Booking(
                org_id=body.org_id, service_id=body.service_id, customer_id=customer_id, call_id=body.call_id,
                service_name=body.service_name, starts_at=body.starts_at, ends_at=body.starts_at + length,
                source=body.source,
            )
            db.add(booking)
        logger.info("booking confirmed: id=%s org=%s", booking.id, body.org_id)
        return BookingResponse(status="confirmed", booking_id=booking.id)
    except IntegrityError as e:
        if "bookings_no_overlap" not in str(e.orig):
            logger.warning("booking failed: %s", type(e.orig).__name__)
            return BookingResponse(status="error")
        return BookingResponse(status="conflict", alternatives=await _free_times(body.org_id, body.starts_at, length))
