"""Database layer shared by the agent and the API (SQLAlchemy 2 async + asyncpg)."""

from db.models import Base, Call, CallEvent, CallTurnMetric
from db.session import database_configured, dispose_engine, session_scope
from db.tenancy import (
    AgentProfile,
    Booking,
    BusinessHours,
    Customer,
    Membership,
    Organization,
    Service,
    User,
    UserSession,
)

__all__ = [
    "AgentProfile",
    "Base",
    "Booking",
    "BusinessHours",
    "Call",
    "CallEvent",
    "CallTurnMetric",
    "Customer",
    "Membership",
    "Organization",
    "Service",
    "User",
    "UserSession",
    "database_configured",
    "dispose_engine",
    "session_scope",
]
