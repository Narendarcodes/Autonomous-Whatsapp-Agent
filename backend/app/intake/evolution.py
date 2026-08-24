"""Evolution API edge adapter — payload -> trusted InboundMessage.

This adapter OWNS knowledge of Evolution API's wire shape (ADR-0007 seam
placement): event names, JID formats, contextInfo nesting, audio detection.
If Evolution changes shape, only this file changes.

`normalize_event` is PURE: the caller resolves IO facts (bot_phone via cache /
Evolution API, bot_mode via preferences) and passes them in. That keeps the
adapter fully unit-testable without Redis, Postgres, or HTTP.
"""
from __future__ import annotations

from typing import Any

from app.intake.types import InboundMessage

VOICE_PLACEHOLDER = "[Voice Message]"

_MESSAGE_EVENTS = ("messages.upsert", "send.message")


def _event_name(payload: dict[str, Any]) -> str:
    return str(payload.get("event") or "").lower().replace("_", ".")


def extract_event_text(message: dict) -> str:
    """Extract display text from an Evolution message object."""
    return (
        message.get("conversation")
        or (message.get("extendedTextMessage") or {}).get("text")
        or (message.get("imageMessage") or {}).get("caption")
        or ""
    ).strip()


def _clean_jid(jid: str) -> str:
    return jid.replace("@s.whatsapp.net", "").replace("@c.us", "").lstrip("+")


def normalize_event(
    payload: dict[str, Any],
    *,
    bot_phone: str | None,
    bot_mode: str,
) -> InboundMessage | None:
    """Normalize a webhook payload into an InboundMessage.

    Returns None when the payload is not a processable inbound message
    (foreign event type, missing JID, outbound echo, empty content).
    """
    if _event_name(payload) not in _MESSAGE_EVENTS:
        return None

    data = payload.get("data") or {}
    key = data.get("key") or {}

    remote_jid = key.get("remoteJid") or ""
    if not remote_jid:
        return None

    from_me = key.get("fromMe") or False
    if _event_name(payload) == "send.message":
        from_me = True

    sender_phone_from_jid = _clean_jid(remote_jid)

    # bot identity fallback: last resort is the payload sender field (pure).
    if not bot_phone:
        bot_phone = _clean_jid(payload.get("sender") or "")

    # Loop prevention for outbound echoes. In self_chat mode messages sent by
    # our own number ARE the conversation; otherwise drop from-me events.
    is_self_chat = (sender_phone_from_jid == bot_phone) if bot_mode == "self_chat" else False
    if from_me and not is_self_chat:
        return None

    msg_obj = data.get("message") or {}
    body = extract_event_text(msg_obj)
    is_audio = "audioMessage" in msg_obj

    if not body and not is_audio:
        return None

    context_info: dict[str, Any] = {}
    if isinstance(msg_obj, dict):
        for k, v in msg_obj.items():
            if isinstance(v, dict) and "contextInfo" in v:
                context_info = v["contextInfo"]
                break
        if not context_info:
            context_info = msg_obj.get("contextInfo") or {}

    quoted_text = ""
    if context_info:
        quoted_msg = context_info.get("quotedMessage")
        if isinstance(quoted_msg, dict):
            quoted_text = extract_event_text(quoted_msg)

    is_group = "@g.us" in remote_jid
    participant = data.get("participant") or ""
    # Group events without participant fall back to the chat JID (legacy parity).
    sender_jid = (participant or remote_jid) if is_group else remote_jid
    chat_id = remote_jid
    sender_phone = _clean_jid(sender_jid)

    return InboundMessage(
        sender_phone=sender_phone,
        chat_id=chat_id,
        is_group=is_group,
        group_id=chat_id if is_group else "",
        message_text=body or VOICE_PLACEHOLDER,
        message_id=key.get("id", ""),
        timestamp=str(data.get("messageTimestamp", "")),
        is_audio=is_audio,
        push_name=data.get("pushName") or "",
        quoted_text=quoted_text,
        bot_phone=bot_phone or None,
        instance=payload.get("instance"),
        bot_mode=bot_mode,
    )
