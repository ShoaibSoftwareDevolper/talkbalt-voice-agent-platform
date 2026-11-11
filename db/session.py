import os
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

_engine: AsyncEngine | None = None
_sessions: async_sessionmaker[AsyncSession] | None = None


def database_configured() -> bool:
    return bool(os.getenv("DATABASE_URL"))


def async_database_url() -> str:
    """DATABASE_URL in the form SQLAlchemy's asyncpg driver expects.

    Accepts the plain `postgresql://` / `postgres://` URLs that local Postgres and Railway
    hand out, and maps libpq's `sslmode` query parameter to asyncpg's `ssl`.
    """
    parts = urlsplit(os.environ["DATABASE_URL"])
    scheme = "postgresql+asyncpg" if parts.scheme in ("postgres", "postgresql") else parts.scheme
    query = [("ssl" if k == "sslmode" else k, v) for k, v in parse_qsl(parts.query)]
    return urlunsplit((scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def get_engine() -> AsyncEngine:
    # Created lazily so each agent job process gets its own engine on its own event loop.
    global _engine, _sessions
    if _engine is None:
        _engine = create_async_engine(async_database_url(), pool_pre_ping=True, pool_size=5)
        _sessions = async_sessionmaker(_engine, expire_on_commit=False)
    return _engine


# NOLOGIN role the RLS policies apply to (created by the accounts migration). Superusers and
# table owners skip row level security, so tenant-scoped sessions switch to this role.
TENANT_ROLE = "talkbalt_app"


@asynccontextmanager
async def session_scope(org_id: uuid.UUID | None = None) -> AsyncIterator[AsyncSession]:
    """A session that commits on success and rolls back on error.

    With `org_id`, the transaction runs as TENANT_ROLE with `app.org_id` set, so row level
    security limits every business table (services, hours, bookings, ...) to that organization.
    """
    get_engine()
    assert _sessions is not None
    async with _sessions() as session, session.begin():
        if org_id is not None:
            await session.execute(text(f"SET LOCAL ROLE {TENANT_ROLE}"))
            await session.execute(text("SELECT set_config('app.org_id', :org, true)"), {"org": str(org_id)})
        yield session


async def dispose_engine() -> None:
    global _engine, _sessions
    if _engine is not None:
        await _engine.dispose()
        _engine = _sessions = None
