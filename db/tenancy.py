"""SaaS tables: accounts (users, organizations, sessions) and each business's setup and bookings.

Every business-owned table carries `org_id`. The tables marked "RLS" below are also protected by
Postgres row level security (see the migration): a session opened with `session_scope(org_id=...)`
only ever sees and writes that organization's rows, even if a query forgets its filter.
"""

import uuid
from datetime import datetime, time

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    Time,
    UniqueConstraint,
    Uuid,
    func,
    text,
    true,
)
from sqlalchemy.dialects.postgresql import CITEXT, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from db.models import Base


def _org_fk() -> Mapped[uuid.UUID]:
    return mapped_column(Uuid, ForeignKey("organizations.id", ondelete="CASCADE"), index=True)


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(CITEXT, unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    name: Mapped[str] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Organization(Base):
    """One business (tenant)."""

    __tablename__ = "organizations"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(160))
    slug: Mapped[str] = mapped_column(String(80), unique=True)
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Karachi")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Membership(Base):
    __tablename__ = "memberships"

    org_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("organizations.id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True, index=True)
    role: Mapped[str] = mapped_column(String(16), default="owner")  # owner | admin | staff


class UserSession(Base):
    """A signed-in browser. Only a SHA-256 of the cookie token is stored."""

    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AgentProfile(Base):
    """How the agent presents this business. RLS."""

    __tablename__ = "agent_profiles"

    org_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("organizations.id", ondelete="CASCADE"), primary_key=True)
    business_name: Mapped[str] = mapped_column(String(160))
    greeting: Mapped[str | None] = mapped_column(String(300))
    about: Mapped[str | None] = mapped_column(Text)  # short description the agent may share with callers
    location_type: Mapped[str] = mapped_column(String(16), default="in_person", server_default="in_person")  # in_person | remote | both
    address: Mapped[str | None] = mapped_column(String(300))
    city: Mapped[str | None] = mapped_column(String(120))
    country: Mapped[str | None] = mapped_column(String(80))
    contact_email: Mapped[str | None] = mapped_column(String(254))
    website: Mapped[str | None] = mapped_column(String(300))
    # Common questions and the answers the agent should give: [{"q": ..., "a": ...}].
    faqs: Mapped[list[dict[str, str]]] = mapped_column(JSONB, default=list, server_default=text("'[]'::jsonb"))
    # Visitors can call through the public link and the website widget.
    public_calls_enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class Service(Base):
    """Something the business offers: a bookable service, or a product callers can ask about. RLS."""

    __tablename__ = "services"
    __table_args__ = (UniqueConstraint("org_id", "name"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = _org_fk()
    kind: Mapped[str] = mapped_column(String(16), default="service", server_default="service")  # service (bookable) | product
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str | None] = mapped_column(String(500))
    duration_min: Mapped[int] = mapped_column(Integer, default=30)  # services only
    price_cents: Mapped[int | None] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3), default="PKR")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)


class BusinessHours(Base):
    """One opening window on one weekday (0 = Monday). A day without rows is closed. RLS."""

    __tablename__ = "business_hours"

    org_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("organizations.id", ondelete="CASCADE"), primary_key=True)
    weekday: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    opens: Mapped[time] = mapped_column(Time, primary_key=True)
    closes: Mapped[time] = mapped_column(Time)


class Customer(Base):
    """Someone who booked. RLS."""

    __tablename__ = "customers"
    __table_args__ = (UniqueConstraint("org_id", "phone_e164"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = _org_fk()
    name: Mapped[str] = mapped_column(String(160))
    phone_e164: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Booking(Base):
    """An appointment. Overlapping active bookings of one business are rejected by an exclusion
    constraint in the database (see the migration), so two calls can never take the same slot. RLS."""

    __tablename__ = "bookings"
    # (org_id, starts_at) serves both "this business's bookings" and calendar range queries.
    __table_args__ = (Index("ix_bookings_org_id_starts_at", "org_id", "starts_at"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("organizations.id", ondelete="CASCADE"))
    service_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("services.id", ondelete="SET NULL"))
    customer_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("customers.id", ondelete="CASCADE"), index=True)
    call_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("calls.id", ondelete="SET NULL"))
    service_name: Mapped[str] = mapped_column(String(120))  # as booked, kept if the service is renamed
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), default="confirmed")  # confirmed | cancelled
    source: Mapped[str] = mapped_column(String(16))  # phone | web | dashboard
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
