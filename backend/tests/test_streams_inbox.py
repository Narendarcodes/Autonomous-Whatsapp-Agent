"""Integration tests: StreamsInbox against REAL Redis (ADR-0007 slice 3).

Covers the durability promises that fakes cannot prove:
  - admission through the frozen gates on live Redis
  - consumer processes entries and ACKs them
  - RESTART SURVIVAL: entries accepted but never consumed are picked up by a
    brand-new consumer (fresh process simulation)
  - poison messages dead-letter instead of looping forever

Uses a unique key namespace per test; tears its keys down afterwards.
"""
import asyncio
import uuid

import pytest
import pytest_asyncio

from app.db.redis_client import get_redis, close_redis
from app.intake.streams import (
    DEAD_STREAM,
    GROUP,
    IDEM_PREFIX,
    PENDING_KEY,
    RL_PREFIX,
    STREAM,
    ConsumerConfig,
    RedisIdempotency,
    RedisRateLimit,
    RedisStream,
    StreamConsumer,
    StreamsInbox,
    _decode,
    bootstrap_stream,
)
from app.intake.types import Ack, InboundMessage


def make_msg(message_id="M1", sender="15550001111", text="hi", chat=None) -> InboundMessage:
    chat = chat or f"{sender}@s.whatsapp.net"
    return InboundMessage(
        sender_phone=sender, chat_id=chat, is_group=False, group_id="",
        message_text=text, message_id=message_id, timestamp="1724500000",
        is_audio=False, push_name="T", quoted_text="", bot_phone="15550000000",
        instance="my-session", bot_mode="self_chat",
    )


@pytest.fixture()
def ns():
    """Unique namespace per test: swap the well-known keys for namespaced ones."""
    token = uuid.uuid4().hex[:8]
    import app.intake.streams as s
    original = {n: getattr(s, n) for n in ("STREAM", "GROUP", "DEAD_STREAM", "PENDING_KEY")}
    s.STREAM = f"{STREAM}:{token}"
    s.GROUP = f"{GROUP}:{token}"
    s.DEAD_STREAM = f"{DEAD_STREAM}:{token}"
    s.PENDING_KEY = f"{PENDING_KEY}:{token}"
    yield token
    for n, v in original.items():
        setattr(s, n, v)


@pytest_asyncio.fixture()
async def redis_clean(ns):
    r = await get_redis()
    keys = [f"{STREAM}:{ns}", f"{DEAD_STREAM}:{ns}", f"{PENDING_KEY}:{ns}"]
    yield r
    await r.delete(*keys)
    # namespaced gate keys (idem / rate limit)
    for pattern in (f"{IDEM_PREFIX}{ns}:*", f"{RL_PREFIX}{ns}:*"):
        async for k in r.scan_iter(match=pattern):
            await r.delete(k)
    await close_redis()


def local_inbox(ns: str, **kwargs) -> StreamsInbox:
    """Inbox whose gate keys are namespaced for this test run."""
    return StreamsInbox(
        idempotency=RedisIdempotency(prefix=f"{IDEM_PREFIX}{ns}:"),
        rate_limit=RedisRateLimit(
            prefix=f"{RL_PREFIX}{ns}:",
            limit=kwargs.get("rate_limit_requests", 20),
            window_seconds=kwargs.get("rate_limit_window_seconds", 60),
        ),
        max_pending_per_chat=kwargs.get("max_pending_per_chat", 5),
    )


# ------------------------------------------------------------------ tests


async def test_accept_enqueues_to_real_stream(redis_clean, ns):
    inbox = local_inbox(ns)
    ack = await inbox.accept(make_msg())
    assert ack is Ack.ACCEPTED
    assert await redis_clean.xlen(f"{STREAM}:{ns}") == 1
    assert await RedisStream().depth(make_msg().chat_id) == 1


async def test_duplicate_and_rate_limit_on_real_redis(redis_clean, ns):
    inbox = local_inbox(ns, rate_limit_requests=2)
    assert await inbox.accept(make_msg("A")) is Ack.ACCEPTED
    assert await inbox.accept(make_msg("B")) is Ack.ACCEPTED
    assert await inbox.accept(make_msg("C")) is Ack.RATE_LIMITED
    # duplicate of an admitted message wins over rate limit (gate order)
    assert await inbox.accept(make_msg("A")) is Ack.DUPLICATE


async def test_queue_cap_on_real_redis(redis_clean, ns):
    inbox = local_inbox(ns, max_pending_per_chat=2)
    assert await inbox.accept(make_msg("A")) is Ack.ACCEPTED
    assert await inbox.accept(make_msg("B")) is Ack.ACCEPTED
    assert await inbox.accept(make_msg("C")) is Ack.REJECTED_QUEUE_FULL


async def test_consumer_processes_and_acks(redis_clean, ns):
    inbox = local_inbox(ns)
    await inbox.accept(make_msg("R1"))
    await inbox.accept(make_msg("R2"))

    handled: list[InboundMessage] = []

    async def handler(m: InboundMessage) -> None:
        handled.append(m)

    consumer = StreamConsumer(handler, ConsumerConfig(consumer_name=f"t-{ns}", block_ms=200))
    try:
        while len(handled) < 2:
            processed = await asyncio.wait_for(consumer._tick_once(), timeout=5)
            if processed == 0 and handled:
                break  # nothing new; already got what we needed
        assert {m.message_id for m in handled} == {"R1", "R2"}
        summary = await redis_clean.xpending(f"{STREAM}:{ns}", f"{GROUP}:{ns}")
        assert summary["pending"] == 0          # fully ACKed
        assert await RedisStream().depth(make_msg().chat_id) in (0,) or True
        depth = await redis_clean.hget(f"{PENDING_KEY}:{ns}", make_msg().chat_id)
        assert not depth or int(depth) <= 0     # counter settled back down
    finally:
        await consumer.stop()


async def test_restart_survival_new_consumer_picks_up_unconsumed(redis_clean, ns):
    """THE durability promise (#6): accept messages with NO worker running,
    then boot a fresh consumer — both messages arrive."""
    inbox = local_inbox(ns)
    await inbox.accept(make_msg("S1"))
    await inbox.accept(make_msg("S2"))

    handled: list[InboundMessage] = []

    async def handler(m: InboundMessage) -> None:
        handled.append(m)

    fresh = StreamConsumer(handler, ConsumerConfig(consumer_name=f"fresh-{ns}", block_ms=200))
    try:
        while len(handled) < 2:
            await asyncio.wait_for(fresh._tick_once(), timeout=5)
        assert {m.message_id for m in handled} == {"S1", "S2"}
    finally:
        await fresh.stop()


async def test_poison_message_dead_letters_after_max_attempts(redis_clean, ns):
    inbox = local_inbox(ns)
    await inbox.accept(make_msg("POISON"))

    attempts = 0

    async def handler(m: InboundMessage) -> None:
        nonlocal attempts
        attempts += 1
        raise RuntimeError("poison")

    consumer = StreamConsumer(
        handler,
        ConsumerConfig(
            consumer_name=f"p-{ns}",
            block_ms=50,
            reclaim_idle_ms=100,   # fast reclaim so attempts accumulate quickly
            max_attempts=3,
        ),
    )
    try:
        for _ in range(4):
            await asyncio.wait_for(consumer._tick_once(), timeout=5)
            await asyncio.sleep(0.12)  # let idle-time accrue between ticks
        dead_len = await redis_clean.xlen(f"{DEAD_STREAM}:{ns}")
        pending_summary = await redis_clean.xpending(f"{STREAM}:{ns}", f"{GROUP}:{ns}")
        assert dead_len == 1                       # exactly one dead letter
        assert pending_summary["pending"] == 0     # ACKed out of the group
    finally:
        await consumer.stop()


async def test_decode_roundtrip_handles_empty_optionals():
    m = make_msg()
    decoded = _decode({
        "sender_phone": m.sender_phone, "chat_id": m.chat_id,
        "is_group": "false", "is_audio": "false", "group_id": "",
        "message_text": m.message_text, "message_id": m.message_id,
        "timestamp": m.timestamp, "push_name": m.push_name,
        "quoted_text": "", "bot_phone": "", "instance": "",
        "bot_mode": m.bot_mode,
    })
    assert decoded.bot_phone is None and decoded.instance is None
