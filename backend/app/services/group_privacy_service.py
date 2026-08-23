"""Group Privacy Service — keeps owner-sensitive data out of group chats.

Two layers:
1. build_group_privacy_directive() — system-prompt guardrail injected when the
   destination is a group, telling Hermes never to expose private data there.
2. redact() — output scrubber (defense-in-depth) that masks emails, phone
   numbers, and long numeric tokens before a reply is sent to a group.
"""
import re

from app.core.logging import get_logger

logger = get_logger(__name__)

# --- Detection -------------------------------------------------------------

def is_group_chat(session_id: str | None) -> bool:
    """True if the session/chat target is a WhatsApp group JID (@g.us)."""
    if not session_id:
        return False
    return session_id.endswith("@g.us")


# --- Prompt-layer guardrail --------------------------------------------------

GROUP_PRIVACY_DIRECTIVE = (
    "[GROUP PRIVACY MODE — HARD CONSTRAINT]\n"
    "You are replying inside a WhatsApp GROUP. Every member of this group can "
    "read your reply. You must NEVER reveal the owner's private information in "
    "this chat, including: calendar events and their details/titles/times, "
    "email contents or senders, document names or contents, personal phone "
    "numbers, email addresses, physical addresses, OTPs/codes/tokens, or any "
    "other data fetched from the owner's connected tools (Calendar, Gmail, "
    "Drive, etc.).\n"
    "- If asked something requiring private data: give a generic answer with NO "
    "private specifics, and tell them to DM you privately for details.\n"
    "- Never confirm or deny the existence of specific private events, emails, "
    "or files.\n"
    "- Keep replies short and neutral; do not narrate tool calls or their "
    "results in this chat."
)


def build_group_privacy_directive() -> str:
    """Return the privacy directive injected into the system prompt for groups."""
    return GROUP_PRIVACY_DIRECTIVE


# --- Output-layer redaction ---------------------------------------------------

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PHONE_RE = re.compile(r"(?<!\w)(?:\+?\d[\d\s().-]{7,14}\d)(?!\w)")
_LONG_NUM_RE = re.compile(r"\d{10,}")

_REDACTED = "[REDACTED]"


def redact(text: str | None) -> str:
    """Mask emails, phone numbers, and long numeric tokens in outbound text."""
    if not text:
        return text or ""
    out = _EMAIL_RE.sub(_REDACTED, text)
    # Mask long raw numeric runs first so they don't get partially eaten by the
    # looser phone pattern; then catch formatted phones.
    out = _LONG_NUM_RE.sub(_REDACTED, out)
    out = _PHONE_RE.sub(_REDACTED, out)
    return out


group_privacy_service = None  # module-level functions; no state needed
