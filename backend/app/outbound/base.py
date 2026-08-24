"""The outbound port. One method; everything else is implementation."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class DeliveryResult:
    ok: bool
    detail: str | None = None

    def __bool__(self) -> bool:  # ergonomic truthiness at call sites
        return self.ok


class WhatsAppOutbound(ABC):
    """Deliver a text message to a WhatsApp chat.

    session_hint: the *inbound* session this reply belongs to
    ("agent-session" / "my-session"), when the caller knows it. The
    implementation owns how hints and owner preferences map to transports.
    """

    @abstractmethod
    async def send(self, chat_id: str, text: str, *, session_hint: str | None = None) -> DeliveryResult:
        """Never raises for delivery failures — failures are values."""
