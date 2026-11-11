"""The platform's Redis: rate limits shared by every API process, and a cache of each business's
runtime setup (what the voice agent loads at the start of a call).

Redis is an accelerator here, never the source of truth: if it is down, rate limits fail open and
the cache is skipped, so the API keeps working on Postgres alone.
"""

import contextlib
import json
import logging
import os
import uuid
from typing import Any

from redis.asyncio import Redis
from redis.exceptions import RedisError

logger = logging.getLogger("talkbalt-platform")

RUNTIME_TTL_SECONDS = 300
_redis: Redis | None = None


def redis() -> Redis | None:
    global _redis
    url = os.getenv("REDIS_URL")
    if not url:
        return None
    if _redis is None:
        _redis = Redis.from_url(url, decode_responses=True, socket_timeout=0.5, socket_connect_timeout=0.5)
    return _redis


async def allow(key: str, limit: int, window_seconds: int) -> bool:
    """At most `limit` hits per `window_seconds` for `key` (fixed window, atomic across processes)."""
    r = redis()
    if r is None:
        return True
    bucket = f"rl:{key}"
    try:
        async with r.pipeline(transaction=True) as pipe:
            count, _ = await pipe.incr(bucket).expire(bucket, window_seconds, nx=True).execute()
        return int(count) <= limit
    except RedisError as e:
        logger.warning("rate limit check skipped, redis unavailable (%s)", type(e).__name__)
        return True


def _runtime_key(org_id: uuid.UUID) -> str:
    return f"biz:{org_id}:runtime"


async def get_runtime(org_id: uuid.UUID) -> dict[str, Any] | None:
    r = redis()
    if r is None:
        return None
    try:
        cached = await r.get(_runtime_key(org_id))
    except RedisError:
        return None
    return json.loads(cached) if cached else None


async def set_runtime(org_id: uuid.UUID, runtime: dict[str, Any]) -> None:
    r = redis()
    if r is None:
        return
    with contextlib.suppress(RedisError):
        await r.set(_runtime_key(org_id), json.dumps(runtime), ex=RUNTIME_TTL_SECONDS)


async def invalidate_runtime(org_id: uuid.UUID) -> None:
    """Called after any change to a business's profile, services or hours."""
    r = redis()
    if r is None:
        return
    try:
        await r.delete(_runtime_key(org_id))
    except RedisError:
        logger.warning("could not invalidate the runtime cache of %s", org_id)
