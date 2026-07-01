"""Redis client + Streams + Sorted Set scheduler helpers."""
import json
from typing import Any

import redis.asyncio as redis_async

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

MESSAGE_STREAM = "message_queue"
MESSAGE_STREAM_GROUP = "agent_workers"
MESSAGE_STREAM_DEAD = "message_queue_dead"

DELAYED_JOBS_KEY = "delayed_jobs"
DELAYED_JOBS_PROCESSING = "delayed_jobs:processing"

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


async def enqueue_message(payload: dict[str, Any]) -> str:
    """Append a message to the agent message stream."""
    r = await get_redis()
    safe_payload = {k: (v if isinstance(v, str) else json.dumps(v)) for k, v in payload.items()}
    msg_id = await r.xadd(MESSAGE_STREAM, safe_payload)
    return msg_id


async def ensure_consumer_group() -> None:
    r = await get_redis()
    try:
        await r.xgroup_create(MESSAGE_STREAM, MESSAGE_STREAM_GROUP, id="0", mkstream=True)
        logger.info("Created consumer group %s on %s", MESSAGE_STREAM_GROUP, MESSAGE_STREAM)
    except redis_async.ResponseError as e:
        if "BUSYGROUP" not in str(e):
            raise


async def schedule_job(job_id: str, run_at_ts: float, payload: dict[str, Any]) -> None:
    """Add a delayed job to the sorted set."""
    r = await get_redis()
    body = json.dumps({"id": job_id, "payload": payload})
    await r.zadd(DELAYED_JOBS_KEY, {body: run_at_ts})


async def fetch_due_jobs(now_ts: float, limit: int = 100) -> list[dict[str, Any]]:
    r = await get_redis()
    members = await r.zrangebyscore(DELAYED_JOBS_KEY, min=0, max=now_ts, start=0, num=limit)
    if not members:
        return []
    async with r.pipeline(transaction=True) as pipe:
        for m in members:
            pipe.zrem(DELAYED_JOBS_KEY, m)
        await pipe.execute()
    return [json.loads(m) for m in members]


async def cancel_jobs_for_event(event_id: str) -> int:
    """Remove all delayed jobs tied to a particular calendar event."""
    r = await get_redis()
    members = await r.zrange(DELAYED_JOBS_KEY, 0, -1)
    removed = 0
    for m in members:
        try:
            data = json.loads(m)
            if data.get("payload", {}).get("event_id") == event_id:
                await r.zrem(DELAYED_JOBS_KEY, m)
                removed += 1
        except Exception:
            continue
    return removed


async def check_idempotency(key: str, ttl_seconds: int = 86400) -> bool:
    """Returns True if this is a NEW request (set the key), False if duplicate."""
    if not key:
        return True
    r = await get_redis()
    was_set = await r.set(f"idem:{key}", "1", ex=ttl_seconds, nx=True)
    return bool(was_set)


async def cache_get(key: str) -> str | None:
    r = await get_redis()
    return await r.get(key)


async def cache_set(key: str, value: str, ttl_seconds: int = 3600) -> None:
    r = await get_redis()
    await r.set(key, value, ex=ttl_seconds)


async def check_rate_limit(sender: str) -> bool:
    """Returns True if request is allowed, False if rate-limited."""
    r = await get_redis()
    key = f"rl:{sender}"
    pipe = r.pipeline()
    pipe.incr(key)
    pipe.expire(key, settings.RATE_LIMIT_WINDOW_SECONDS)
    result = await pipe.execute()
    return int(result[0]) <= settings.RATE_LIMIT_REQUESTS
