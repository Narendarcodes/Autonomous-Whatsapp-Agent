"""Redis client helpers (cache + legacy webhook gates).

Durable intake queueing lives in app/intake/streams.py (ADR-0007). The
former custom scheduler helpers (schedule_job / fetch_due_jobs /
cancel_jobs_for_event / enqueue_message / ensure_consumer_group) were
deleted per ADR-0001/0002: Hermes native cron owns proactive scheduling,
and the intake stream owns message queueing.
"""
from typing import Any

import redis.asyncio as redis_async

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

_redis: redis_async.Redis | None = None


async def get_redis() -> redis_async.Redis:
    global _redis
    if _redis is None:
        _redis = redis_async.from_url(
            settings.redis_url,
            decode_responses=True,
            socket_connect_timeout=5,
            socket_timeout=10,
        )
        await _redis.ping()
    return _redis


async def close_redis() -> None:
    global _redis
    if _redis is not None:
        try:
            await _redis.aclose()
        except RuntimeError as e:
            if "Event loop is closed" not in str(e):
                raise
        finally:
            _redis = None


async def cache_get(key: str) -> str | None:
    r = await get_redis()
    return await r.get(key)


async def cache_set(key: str, value: str, ttl_seconds: int = 3600) -> None:
    r = await get_redis()
    await r.set(key, value, ex=ttl_seconds)
