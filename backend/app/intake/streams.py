"""Durable Redis Streams adapter behind the Inbox seam (ADR-0007).

Production adapters for every dependency port plus the consumer machinery:

  - RedisIdempotency / RedisRateLimit / RedisSentLog : gate dependencies
  - RedisStream                                      : StreamPort (enqueue/depth)
  - StreamsInbox                                     : the Inbox itself
  - StreamConsumer                                   : single-consumer loop with
        PENDING re-claim on boot (restart survival), poison-message dead-lettering

Per-chat FIFO holds because one consumer processes strictly sequentially;
the consumer group keeps chat-hash partitioning a config-level change later.
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field, fields as dc_fields

from app.db.redis_client import get_redis
from app.intake.base import IdempotencyPort, Inbox, RateLimitPort, SentLogPort, StreamPort
from app.intake.gates import admit
from app.intake.policy import PolicyFn
from app.intake.types import Ack, InboundMessage

logger = logging.getLogger(__name__)

STREAM = "omniwa:inbound"
GROUP = "agent_workers"
DEAD_STREAM = "omniwa:inbound:dead"
PENDING_KEY = "inbox:pending"          # hash: chat_id -> pending count
IDEM_PREFIX = "inbox:idem:"            # + dedupe key, TTL 24h
RL_PREFIX = "inbox:rl:"                # + sender


# ----------------------------------------------------------- field codec


def _encode(message: InboundMessage) -> dict[str, str]:
    out: dict[str, str] = {}
    for f in dc_fields(message):
        v = getattr(message, f.name)
        if v is None:
            out[f.name] = ""
        elif isinstance(v, bool):
            out[f.name] = json.dumps(v)
        else:
            out[f.name] = str(v)
    return out


def _decode(fields: dict[str, str]) -> InboundMessage:
    kwargs: dict = {}
    for f in dc_fields(InboundMessage):
        raw = fields.get(f.name, "")
        if f.name in ("is_group", "is_audio"):
            kwargs[f.name] = raw == "true"
        elif f.name in ("bot_phone", "instance"):
            kwargs[f.name] = raw or None
        else:
            kwargs[f.name] = raw
    return InboundMessage(**kwargs)


# ---------------------------------------------------------------- ports


class RedisIdempotency(IdempotencyPort):
    def __init__(self, ttl_seconds: int = 86400, prefix: str = IDEM_PREFIX) -> None:
        self.ttl_seconds = ttl_seconds
        self.prefix = prefix

    async def seen_before(self, key: str) -> bool:
        r = await get_redis()
        first_time = await r.set(f"{self.prefix}{key}", "1", ex=self.ttl_seconds, nx=True)
        return not bool(first_time)


class RedisRateLimit(RateLimitPort):
    """Fixed-window limiter whose TTL starts at the FIRST hit of the window.

    Unlike the legacy INCR+EXPIRE-every-hit helper, steady traffic no longer
    extends its own window forever (#5 semantics fixed at the seam).
    """

    def __init__(
        self,
        limit: int = 20,
        window_seconds: int = 60,
        prefix: str = RL_PREFIX,
    ) -> None:
        self.limit = limit
        self.window_seconds = window_seconds
        self.prefix = prefix

    async def allow(self, sender: str) -> bool:
        r = await get_redis()
        key = f"{self.prefix}{sender}"
        pipe = r.pipeline(transaction=False)
        pipe.incr(key)
        pipe.ttl(key)
        count, ttl = await pipe.execute()
        if int(ttl or -1) < 0:
            await r.expire(key, self.window_seconds)
        return int(count) <= self.limit


class RedisSentLog(SentLogPort):
    """Reads the outbound-send markers written by the send paths.

    Keys kept identical to the legacy guard so existing markers remain valid.
    Recording moves behind the outbound seam in candidate 2.
    """

    async def is_own_send(self, message_id: str, text_hash: str) -> bool:
        r = await get_redis()
        if message_id and await r.get(f"sent_message:{message_id}"):
            return True
        if text_hash and await r.get(f"sent_text:{text_hash}"):
            return True
        return False


class RedisStream(StreamPort):
    async def enqueue(self, chat_id: str, message: InboundMessage) -> None:
        r = await get_redis()
        pipe = r.pipeline(transaction=True)
        pipe.xadd(STREAM, _encode(message))
        pipe.hincrby(PENDING_KEY, chat_id, 1)
        await pipe.execute()

    async def depth(self, chat_id: str) -> int:
        r = await get_redis()
        return int(await r.hget(PENDING_KEY, chat_id) or 0)

    async def complete(self, chat_id: str) -> None:
        r = await get_redis()
        await r.hincrby(PENDING_KEY, chat_id, -1)


class StreamsInbox(Inbox):
    """Production Inbox. Same frozen gates; durable internals."""

    def __init__(
        self,
        *,
        idempotency: IdempotencyPort | None = None,
        rate_limit: RateLimitPort | None = None,
        sent_log: SentLogPort | None = None,
        stream: StreamPort | None = None,
        session_policy: PolicyFn | None = None,
        rate_limit_requests: int = 20,
        rate_limit_window_seconds: int = 60,
        max_pending_per_chat: int = 5,
    ) -> None:
        from app.intake.policy import evolution_session_policy

        self._idem = idempotency or RedisIdempotency()
        self._rl = rate_limit or RedisRateLimit(rate_limit_requests, rate_limit_window_seconds)
        self._log = sent_log or RedisSentLog()
        self._stream = stream or RedisStream()
        self._policy = session_policy or evolution_session_policy
        self._max_pending = max_pending_per_chat

    async def accept(self, message: InboundMessage) -> Ack:
        # session-policy pre-gates run before admission (legacy router rules)
        policy_outcome = await self._policy(message)
        if policy_outcome is not None:
            return policy_outcome
        return await admit(
            message,
            idempotency=self._idem,
            rate_limit=self._rl,
            sent_log=self._log,
            stream=self._stream,
            max_pending_per_chat=self._max_pending,
        )


# ------------------------------------------------------------- consumer


Handler = callable  # async callable(InboundMessage) -> None; raise on failure


@dataclass
class ConsumerConfig:
    consumer_name: str = "c1"
    block_ms: int = 2000
    batch: int = 10
    reclaim_idle_ms: int = 300_000      # re-claim entries stuck >5 min
    max_attempts: int = 5               # then dead-letter


class StreamConsumer:
    """Single sequential consumer with restart survival and DLQ."""

    def __init__(self, handler: Handler, config: ConsumerConfig | None = None) -> None:
        self._handler = handler
        self._cfg = config or ConsumerConfig()
        self._task: asyncio.Task | None = None
        self._stopping = asyncio.Event()
        self._attempts: dict[str, int] = {}
        self._booted = False

    # -- lifecycle

    async def start(self) -> None:
        await bootstrap_stream()
        self._stopping.clear()
        self._task = asyncio.create_task(self._run(), name="intake-stream-consumer")
        logger.info("Intake consumer started (%s)", self._cfg.consumer_name)

    async def stop(self) -> None:
        self._stopping.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001 - shutdown path
                pass
        logger.info("Intake consumer stopped")

    # -- loop

    async def _run(self) -> None:
        while not self._stopping.is_set():
            try:
                await self._tick_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # keep the loop alive on transient errors
                logger.error("Consumer tick failed: %s", exc)
                await asyncio.sleep(1)

    async def _tick_once(self) -> int:
        """One cycle: re-claim stuck entries, then read new ones.

        Returns number of entries processed. Also drives the integration tests.
        """
        if not self._booted:
            await bootstrap_stream()
            self._booted = True
        r = await get_redis()
        processed = 0

        await self._reclaim(r)
        resp = await r.xreadgroup(
            GROUP,
            self._cfg.consumer_name,
            {STREAM: ">"},
            count=self._cfg.batch,
            block=self._cfg.block_ms,
        )
        for _stream, entries in resp or []:
            for entry_id, flds in entries:
                await self._process(r, entry_id, flds)
                processed += 1
        return processed

    async def _reclaim(self, r) -> None:
        """Re-claim stuck PENDING entries (crash survival / worker loss)."""
        cursor = "0-0"
        while True:
            resp = await r.xautoclaim(
                STREAM, GROUP, self._cfg.consumer_name,
                min_idle_time=self._cfg.reclaim_idle_ms, start_id=cursor,
                count=self._cfg.batch,
            )
            next_cursor, entries = resp[0], resp[1]
            for entry_id, flds in entries:
                await self._process(r, entry_id, flds)
            if next_cursor == "0-0" or not entries:
                break
            cursor = next_cursor

    async def _process(self, r, entry_id: str, flds: dict[str, str]) -> None:
        message = _decode(flds)
        try:
            await self._handler(message)
        except Exception as exc:
            attempts = self._attempts.get(entry_id, 1) + 1
            self._attempts[entry_id] = attempts
            logger.error("Handler failed for %s (%s/%s): %s", entry_id, attempts, self._cfg.max_attempts, exc)
            if attempts >= self._cfg.max_attempts:
                await r.xadd(DEAD_STREAM, {"entry": entry_id, **flds})
                await r.xack(STREAM, GROUP, entry_id)
                await self._mark_complete(r, message.chat_id)
                self._attempts.pop(entry_id, None)
                logger.warning("Dead-lettered %s after %s attempts", entry_id, attempts)
            return  # leave unacked -> redelivered/reclaimed
        await r.xack(STREAM, GROUP, entry_id)
        await self._mark_complete(r, message.chat_id)
        self._attempts.pop(entry_id, None)

    async def _mark_complete(self, r, chat_id: str) -> None:
        n = await r.hincrby(PENDING_KEY, chat_id, -1)
        if n <= 0:
            await r.hdel(PENDING_KEY, chat_id)


async def bootstrap_stream() -> None:
    """Create stream + consumer group idempotently (safe on every boot)."""
    from redis.asyncio import ResponseError

    r = await get_redis()
    try:
        await r.xgroup_create(STREAM, GROUP, id="0", mkstream=True)
        logger.info("Created intake consumer group %s on %s", GROUP, STREAM)
    except ResponseError as e:
        if "BUSYGROUP" not in str(e):
            raise
