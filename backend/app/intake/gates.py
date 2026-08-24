"""Admission gates — the frozen sync sequence every Inbox implementation shares.

ADR-0007 order (load-bearing, do not reorder):
    idempotency -> rate limit -> loop guard -> queue cap
"""
from __future__ import annotations

from app.intake.base import IdempotencyPort, RateLimitPort, SentLogPort, StreamPort
from app.intake.types import Ack, InboundMessage


def text_fingerprint(chat_id: str, message_text: str) -> str:
    """Stable content fingerprint for loop-guard matching.

    NOTE (#11): replaced at slice 4 by per-instance provider message ids;
    kept as a port-level concept so the gate sequence does not change.
    """
    import hashlib

    return hashlib.md5(f"{chat_id}:{message_text.strip()}".encode()).hexdigest()


async def admit(
    message: InboundMessage,
    *,
    idempotency: IdempotencyPort,
    rate_limit: RateLimitPort,
    sent_log: SentLogPort,
    stream: StreamPort,
    max_pending_per_chat: int,
) -> Ack:
    """Run the frozen admission sequence. Returns the Ack; never raises for
    policy outcomes. Transport/infra errors propagate to the caller."""
    key = message.dedupe_key()
    if key and await idempotency.seen_before(key):
        return Ack.DUPLICATE

    if not await rate_limit.allow(message.sender_phone):
        return Ack.RATE_LIMITED

    if await sent_log.is_own_send(message.message_id, text_fingerprint(message.chat_id, message.message_text)):
        return Ack.IGNORED

    if await stream.depth(message.chat_id) >= max_pending_per_chat:
        return Ack.REJECTED_QUEUE_FULL

    await stream.enqueue(message.chat_id, message)
    return Ack.ACCEPTED
