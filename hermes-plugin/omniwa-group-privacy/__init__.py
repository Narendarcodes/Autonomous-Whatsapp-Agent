"""omniWA group privacy — Hermes gateway plugin.

Why: in the omniWA stack the Hermes Baileys bridge talks to WhatsApp
directly (WHATSAPP_GROUP_POLICY=allowlist, HERMES_OWNS_WHATSAPP=true on the
backend). Group replies are generated and delivered inside this container,
so the FastAPI-side group_privacy_service never sees them. This plugin
enforces the same policy at the source:

1. pre_llm_call        — when the active chat is a WhatsApp GROUP, inject a
                         hard privacy directive into the turn so the model
                         never narrates or exposes owner-private data.
2. transform_llm_output — scrub emails, phone numbers, and long numeric
                         tokens from the FINAL reply before delivery when
                         the active chat is a WhatsApp GROUP (defense in
                         depth against prompt-level misses).

Detection uses the gateway's task-local session contextvars
(gateway.session_context.get_session_env), which are set per incoming
message and visible to both hooks:
  - HERMES_SESSION_CHAT_ID ends with "@g.us", or
  - HERMES_SESSION_KEY contains ":whatsapp:group:"
Non-group chats (DMs, CLI, cron, api_server) are untouched.
"""

import re

_PLUGIN_NAME = "omniwa-group-privacy"

GROUP_PRIVACY_DIRECTIVE = (
    "[GROUP PRIVACY MODE — HARD CONSTRAINT]\n"
    "You are replying inside a WhatsApp GROUP. Every member of this group can "
    "read everything you write, including partial messages while you work. You "
    "must NEVER reveal the owner's private information here, including: "
    "calendar events or their titles/times/attendees, email contents or "
    "addresses, document names or contents, personal phone numbers, physical "
    "addresses, OTPs/codes/tokens, or any data fetched from the owner's "
    "connected tools.\n"
    "- If asked something requiring private data: give a generic answer with NO "
    "private specifics and tell them to DM you privately for details.\n"
    "- Never confirm or deny the existence of specific private events, emails, "
    "or files.\n"
    "- Do not narrate tool calls or their results in this chat."
)

_REDACTED = "[REDACTED]"
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_LONG_NUM_RE = re.compile(r"\d{10,}")
_PHONE_RE = re.compile(r"(?<!\w)(?:\+?\d[\d\s().-]{7,14}\d)(?!\w)")


def _active_chat_is_whatsapp_group() -> bool:
    """True when the current turn targets a WhatsApp group chat."""
    try:
        from gateway.session_context import get_session_env

        chat_id = get_session_env("HERMES_SESSION_CHAT_ID") or ""
        session_key = get_session_env("HERMES_SESSION_KEY") or ""
    except Exception:
        return False
    if chat_id.strip().endswith("@g.us"):
        return True
    return ":whatsapp:group:" in f":{session_key}"


def redact_group_text(text):
    """Mask emails, phone numbers, and long numeric tokens. Hook-safe."""
    if not text or not isinstance(text, str):
        return None
    out = _EMAIL_RE.sub(_REDACTED, text)
    out = _LONG_NUM_RE.sub(_REDACTED, out)
    out = _PHONE_RE.sub(_REDACTED, out)
    return out if out != text else None  # None => leave response unchanged


def register(ctx):
    """Called by Hermes PluginManager at discovery time."""

    def _pre_llm_call(**kwargs):
        if not _active_chat_is_whatsapp_group():
            return None
        return {"context": GROUP_PRIVACY_DIRECTIVE}

    def _transform_llm_output(response_text=None, **kwargs):
        if not _active_chat_is_whatsapp_group():
            return None
        return redact_group_text(response_text)

    ctx.register_hook("pre_llm_call", _pre_llm_call)
    ctx.register_hook("transform_llm_output", _transform_llm_output)
