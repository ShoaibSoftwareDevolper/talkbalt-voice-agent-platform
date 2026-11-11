"""What anyone may see and do with a business's public link (/b/<slug>) and website widget:
read its public info and start a call with its agent. No sign-in, so calls are rate limited (Redis)."""

from datetime import time as clock_time

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select

from api import cache
from api.org import CallToken, Faq, issue_call_token
from db import AgentProfile, BusinessHours, Organization, Service, session_scope

router = APIRouter(prefix="/api/public", tags=["public"])


# Each call costs speech and LLM minutes, so one visitor and one business are both capped.
VISITOR_LIMIT = (5, 600)  # calls per visitor per 10 minutes
BUSINESS_LIMIT = (60, 3600)  # calls per business per hour


def _client_ip(request: Request) -> str:
    # The Next.js proxy (frontend-web/app/api/backend) sets X-Forwarded-For to the visitor's address.
    forwarded = request.headers.get("x-forwarded-for", "")
    return forwarded.split(",")[0].strip() or (request.client.host if request.client else "unknown")


class PublicItem(BaseModel):
    name: str
    description: str | None
    duration_min: int | None
    price_cents: int | None
    currency: str


class PublicHours(BaseModel):
    weekday: int
    opens: clock_time
    closes: clock_time


class PublicBusiness(BaseModel):
    slug: str
    name: str
    about: str | None
    location_type: str
    address: str | None
    city: str | None
    country: str | None
    website: str | None
    contact_email: str | None
    timezone: str
    services: list[PublicItem]
    products: list[PublicItem]
    hours: list[PublicHours]
    faqs: list[Faq]
    calls_enabled: bool


async def _org_by_slug(slug: str) -> Organization:
    async with session_scope() as db:
        org = await db.scalar(select(Organization).where(Organization.slug == slug))
    if org is None:
        raise HTTPException(status_code=404, detail="This business link doesn't exist")
    return org


@router.get("/{slug}")
async def public_business(slug: str) -> PublicBusiness:
    org = await _org_by_slug(slug)
    async with session_scope(org.id) as db:
        profile = await db.get(AgentProfile, org.id)
        items = (await db.scalars(select(Service).where(Service.active).order_by(Service.sort_order, Service.name))).all()
        hours = (await db.scalars(select(BusinessHours).order_by(BusinessHours.weekday, BusinessHours.opens))).all()
    assert profile is not None

    def item(s: Service) -> PublicItem:
        return PublicItem(
            name=s.name, description=s.description, price_cents=s.price_cents, currency=s.currency,
            duration_min=s.duration_min if s.kind == "service" else None,
        )

    return PublicBusiness(
        slug=org.slug, name=profile.business_name, about=profile.about, location_type=profile.location_type,
        address=profile.address, city=profile.city, country=profile.country, website=profile.website,
        contact_email=profile.contact_email, timezone=org.timezone,
        services=[item(s) for s in items if s.kind == "service"],
        products=[item(s) for s in items if s.kind == "product"],
        hours=[PublicHours(weekday=h.weekday, opens=h.opens, closes=h.closes) for h in hours],
        faqs=[Faq(**f) for f in profile.faqs], calls_enabled=profile.public_calls_enabled,
    )


@router.post("/{slug}/call", status_code=201)
async def public_call(slug: str, request: Request) -> CallToken:
    org = await _org_by_slug(slug)
    async with session_scope(org.id) as db:
        profile = await db.get(AgentProfile, org.id)
    if profile is None or not profile.public_calls_enabled:
        raise HTTPException(status_code=403, detail="This business isn't taking calls online right now")
    if not await cache.allow(f"call:{slug}:{_client_ip(request)}", *VISITOR_LIMIT):
        raise HTTPException(status_code=429, detail="Too many calls from your connection. Please try again in a few minutes.")
    if not await cache.allow(f"call:{slug}", *BUSINESS_LIMIT):
        raise HTTPException(status_code=429, detail="This business is getting a lot of calls. Please try again shortly.")
    return issue_call_token(org.id, "web")
