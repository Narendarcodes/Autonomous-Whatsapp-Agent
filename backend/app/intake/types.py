"""Message intake types — the Inbox seam's vocabulary (ADR-0007)."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from enum import Enum


class Ack(str, Enum):
    """Admission outcome. Describes ADMITTED-or-not ONLY — never delivery.

    Post-admission gates (DPDP, ACL, quiet hours, commands, dispatch) run
    asynchronously inside the Inbox implementation; their outcomes never
    appear here.
    """

    ACCEPTED = "accepted"                        # admitted to the durable stream
    DUPLICATE = "duplicate"                      # idempotency gate rejected replay
    RATE_LIMITED = "rate_limited"                # sender exceeded budget
    REJECTED_QUEUE_FULL = "rejected_queue_full"  # per-chat pending cap reached
    IGNORED = "ignored"                          # parsed, but policy says skip
                                                 # (loop guard / session policy)


@dataclass(frozen=True)
class InboundMessage:
    """Trusted, normalized inbound WhatsApp message.

    Produced by an edge adapter (Evolution payload -> domain type). Once
    constructed it is immutable; every gate downstream receives the same
    facts. Field names mirror the legacy parsed-dict keys so call sites can
    migrate mechanically.
    """

    sender_phone: str
    chat_id: str
    is_group: bool
    group_id: str
    message_text: str
    message_id: str
    timestamp: str
    is_audio: bool
    push_name: str
    quoted_text: str
    bot_phone: str | None
    instance: str | None
    bot_mode: str

    def as_dict(self) -> dict:
        """Legacy dict shape for call sites not yet migrated off subscript access."""
        return asdict(self)

    def dedupe_key(self, instance: str | None = None) -> str:
        """Idempotency key: instance-scoped provider message id."""
        scope = instance or self.instance or "default"
        return f"{scope}:{self.message_id}" if self.message_id else ""
