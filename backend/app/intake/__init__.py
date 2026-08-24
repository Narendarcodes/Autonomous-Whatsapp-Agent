"""omniWA message intake — the Inbox seam (ADR-0007).

Public surface:
    InboundMessage, Ack          — types
    Inbox                        — the deep module's interface
    MemoryInbox / make_inbox     — in-memory implementation + fakes (tests)
    normalize_event              — Evolution edge adapter (pure)
"""
from app.intake.base import IdempotencyPort, Inbox, RateLimitPort, SentLogPort, StreamPort
from app.intake.evolution import normalize_event
from app.intake.fakes import (
    FakeHermes,
    FakeIdempotency,
    FakeOutbound,
    FakeRateLimit,
    FakeSentLog,
    FakeStream,
    make_inbox,
)
from app.intake.gates import admit, text_fingerprint
from app.intake.policy import evolution_session_policy
from app.intake.stages import MessagePipeline, get_or_create_user
from app.intake.types import Ack, InboundMessage

__all__ = [
    "Ack",
    "FakeHermes",
    "FakeIdempotency",
    "FakeOutbound",
    "FakeRateLimit",
    "FakeSentLog",
    "FakeStream",
    "IdempotencyPort",
    "InboundMessage",
    "Inbox",
    "MessagePipeline",
    "RateLimitPort",
    "SentLogPort",
    "StreamPort",
    "admit",
    "evolution_session_policy",
    "get_or_create_user",
    "make_inbox",
    "normalize_event",
    "text_fingerprint",
]
