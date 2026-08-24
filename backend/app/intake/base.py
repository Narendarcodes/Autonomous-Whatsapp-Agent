"""Inbox port and its dependency ports (ADR-0007).

The Inbox is THE deep module of message intake. Its interface:
    accept(msg: InboundMessage) -> Ack      plus consumer lifecycle.

Everything else here is a dependency port the implementation requires.
Dependency categories (see architecture review):
    - Idempotency / RateLimit / SentLog / Stream : remote-but-owned (Redis)
      -> production adapters hit Redis; tests use in-memory fakes.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from app.intake.types import Ack, InboundMessage


class IdempotencyPort(ABC):
    """Records a key atomically and reports whether it was seen before."""

    @abstractmethod
    async def seen_before(self, key: str) -> bool:
        """True if key existed already (and leaves it recorded); records otherwise."""


class RateLimitPort(ABC):
    @abstractmethod
    async def allow(self, sender: str) -> bool:
        """True if the sender has budget left under the configured window."""


class SentLogPort(ABC):
    """Record of messages THIS deployment sent, used to drop echo loops."""

    @abstractmethod
    async def is_own_send(self, message_id: str, text_hash: str) -> bool:
        """True if either the provider message id or the content hash matches an
        outbound send we made."""


class StreamPort(ABC):
    """Durable per-chat ordered queue behind the seam."""

    @abstractmethod
    async def enqueue(self, chat_id: str, message: InboundMessage) -> None: ...

    @abstractmethod
    async def depth(self, chat_id: str) -> int:
        """Number of pending (not yet processed) messages for the chat."""


class Inbox(ABC):
    """THE intake interface. Callers know nothing else — see ADR-0007."""

    @abstractmethod
    async def accept(self, message: InboundMessage) -> Ack:
        """Admit a trusted inbound message. Fast — never blocks on Hermes."""
