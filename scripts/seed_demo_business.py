"""Creates or updates the Talkbalt demo business: the receptionist behind "Try a demo call" on the
Talkbalt website. It runs on the same widget every business gets, but its business is Talkbalt
itself, so it only talks about Talkbalt. Safe to run again: it updates in place.

    cd platform-api && uv run python scripts/seed_demo_business.py

The demo business has no members (nobody logs in to it) and no services: it is a conversation, not a
booking line. The receptionist says it is a demo so visitors can hear how Talkbalt would sound for
their own business, and answers questions about Talkbalt.
Keep the facts here in step with what Talkbalt actually does.
"""

import asyncio
import sys
from datetime import time
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
load_dotenv(Path(__file__).resolve().parent.parent / ".env.local")

from sqlalchemy import delete, select  # noqa: E402

from api import cache  # noqa: E402
from db import AgentProfile, BusinessHours, Organization, Service, session_scope  # noqa: E402

SLUG = "talkbalt-demo"  # the website's widget points at this (NEXT_PUBLIC_DEMO_BUSINESS)

PROFILE = {
    "business_name": "Talkbalt",
    "greeting": "Hi! This is a Talkbalt demo call, so you can hear how Talkbalt would answer calls for your business. Ask me anything about Talkbalt.",
    "about": (
        "Talkbalt is an AI receptionist for small businesses in Pakistan and around the world. Customers talk to it "
        "by voice from a link or a button on the business website, in Urdu, Hindi or English. It answers questions "
        "about the business's services, prices and opening hours, and books appointments into the business dashboard. "
        "This call is a demo so visitors can experience how Talkbalt would perform for their own business: talking "
        "naturally, answering questions and handling the conversation. It only talks about Talkbalt and does not "
        "book appointments on this line; for a real business, the receptionist books into its own calendar."
    ),
    "location_type": "remote",
    "country": "Pakistan",
    "language": "en",
    "voice": "kiara",
    "public_calls_enabled": True,
    "widget_label": "Try a demo call",
    "widget_color": "#16181d",
    "widget_position": "right",
    "faqs": [
        {"q": "What is Talkbalt?", "a": "An AI receptionist for businesses. Customers talk to it by voice from a link or a website button, and it answers questions and books appointments."},
        {"q": "Which languages does it speak?", "a": "It understands Urdu, Hindi and English, even mixed together. Each business chooses one of the three for the receptionist to speak."},
        {"q": "Do customers need an app or a phone number?", "a": "No. Customers open the business link or press the call button on its website and talk in their browser. Talkbalt is not a phone line."},
        {"q": "How does booking work?", "a": "It asks for the name, the service, the day, the time and a phone number, one at a time. It checks the opening hours, reads the number back and never books two people in the same slot. The booking appears in the business dashboard with a WhatsApp button."},
        {"q": "How long does setup take?", "a": "About ten minutes: business info, services and prices, opening hours, a language and voice, then a test call."},
        {"q": "How much does Talkbalt cost?", "a": "Plans are being finalised. Businesses can sign up on the Talkbalt website now."},
        {"q": "Which businesses is it for?", "a": "Any business that books appointments, such as salons, clinics, dentists, gyms, academies, workshops and consultants. Today it books one appointment per time slot for each business."},
        {"q": "Does it send SMS or emails?", "a": "No. The business sees every booking in its dashboard and can message the customer on WhatsApp in one tap."},
        {"q": "What is this call?", "a": "A demo call, so you can experience how Talkbalt would talk to your customers. On your own business line, it would also answer from your services, prices and hours and book appointments."},
        {"q": "Can I book an appointment on this call?", "a": "Not on this demo line. When you set up Talkbalt for your business, your receptionist books appointments for your customers."},
        {"q": "How do I get started?", "a": "Sign up on the Talkbalt website, add your services and hours, and share your link."},
    ],
}
# No services: the demo talks about Talkbalt but does not book.
SERVICES: list[dict[str, object]] = []
# Open every day, all day, so the demo reads as available whenever a visitor tries it.
HOURS = [(day, time(0, 0), time(23, 59)) for day in range(7)]


async def main() -> None:
    async with session_scope() as db:
        org = await db.scalar(select(Organization).where(Organization.slug == SLUG))
        if org is None:
            org = Organization(name="Talkbalt", slug=SLUG, timezone="Asia/Karachi")
            db.add(org)
            await db.flush()
        org_id = org.id

    async with session_scope(org_id) as db:
        profile = await db.get(AgentProfile, org_id)
        if profile is None:
            profile = AgentProfile(org_id=org_id, business_name=PROFILE["business_name"])
            db.add(profile)
        for field, value in PROFILE.items():
            setattr(profile, field, value)

        await db.execute(delete(Service).where(Service.org_id == org_id))
        db.add_all(
            Service(org_id=org_id, kind="service", currency="PKR", sort_order=i, active=True, **svc)
            for i, svc in enumerate(SERVICES)
        )
        await db.execute(delete(BusinessHours).where(BusinessHours.org_id == org_id))
        db.add_all(BusinessHours(org_id=org_id, weekday=d, opens=o, closes=c) for d, o, c in HOURS)

    await cache.invalidate_runtime(org_id)
    print(f"Demo business ready: /b/{SLUG} (org {org_id})")


if __name__ == "__main__":
    asyncio.run(main())
