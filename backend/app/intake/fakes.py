"""In-memory adapters for tests — the seam's second adapter makes it real.

Per DEEPENING.md: production uses Redis-backed adapters; tests use these.
No test needs live Redis/Postgres/Hermes to exercise intake semantics.
"""
from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field

from app.intake.base import IdempotencyPort, Inbox, RateLimitPort, SentLogPort, StreamPort
from app.intake.gates import admit
from app.intake.types import Ack, InboundMessage


@dataclass
class FakeIdempotency(IdempotencyPort):
    seen: set[str] = field(default_factory=set)

    async def seen_before(self, key: str) -> bool:
        if key in self.seen:
            return True
        self.seen.add(key)
        return False


@dataclass
class FakeRateLimit(RateLimitPort):
    limit: int = 20
    hits: dict[str, int] = field(default_factory=dict)

    async def allow(self, sender: str) -> bool:
        n = self.hits.get(sender, 0) + 1
        self.hits[sender] = n
        return n <= self.limit


@dataclass
class FakeSentLog(SentLogPort):
    sends: set[tuple[str, str]] = field(default_factory=set)  # (message_id, hash)

    def record(self, message_id: str, fingerprint: str) -> None:
        self.sends.add((message_id, fingerprint))

    async def is_own_send(self, message_id: str, text_hash: str) -> bool:
        if message_id and any(m == message_id for m, _ in self.sends):
            return True
        return any(h == text_hash for _, h in self.sends)


@dataclass
class FakeStream(StreamPort):
    """Ordered per-chat queue with optional global pending cap."""

    max_pending: int | None = None
    queues: dict[str, deque[InboundMessage]] = field(default_factory=lambda: defaultdict(deque))

    async def enqueue(self, chat_id: str, message: InboundMessage) -> None:
        self.queues[chat_id].append(message)

    async def depth(self, chat_id: str) -> int:
        return len(self.queues[chat_id])

    def total_pending(self) -> int:
        return sum(len(q) for q in self.queues.values())


@dataclass
class FakeOutbound:
    """Records outbound sends; later slices route replies through it."""

    sent: list[tuple[str, str]] = field(default_factory=list)  # (chat_id, text)

    async def send(self, chat_id: str, text: str) -> None:
        self.sent.append((chat_id, text))


@dataclass
class FakeHermes:
    """Records dispatches; `fail_next` simulates Hermes being down."""

    dispatched: list[tuple[str, str]] = field(default_factory=list)  # (session_id, text)
    fail_next: int = 0

    async def dispatch(self, session_id: str, text: str) -> bool:
        if self.fail_next > 0:
            self.fail_next -= 1
            return False
        self.dispatched.append((session_id, text))
        return True


def make_inbox(
    *,
    rate_limit: int = 20,
    max_pending_per_chat: int = 5,
    own_sends: set[tuple[str, str]] | None = None,
) -> tuple[Inbox, dict]:
    """Build an in-memory Inbox wired to fresh fakes; returns (inbox, fakes dict)."""
    idem, rl, log, stream = (
        FakeIdempotency(),
        FakeRateLimit(limit=rate_limit),
        FakeSentLog(sends=own_sends or set()),
        FakeStream(),
    )

    class MemoryInbox(Inbox):
        async def accept(self, message: InboundMessage) -> Ack:
            return await admit(
                message,
                idempotency=idem,
                rate_limit=rl,
                sent_log=log,
                stream=stream,
                max_pending_per_chat=max_pending_per_chat,
            )

    inbox = MemoryInbox()
    fakes = {"idempotency": idem, "rate_limit": rl, "sent_log": log, "stream": stream}
    return inbox, fakes
