"""Evolution API webhook receiver.

Evolution API event payload (MESSAGES_UPSERT):
{
  "event": "messages.upsert",
  "instance": "my-session",
  "data": {
    "key": {
      "remoteJid": "919xxx@s.whatsapp.net"  or  "123-456@g.us",
      "fromMe": false,
      "id": "ABCD1234"
    },
    "message": {
      "conversation": "Hello"         ← plain text
      # or
      "extendedTextMessage": { "text": "Hello" }
    },
    "messageType": "conversation",
    "messageTimestamp": 1700000000,
    "pushName": "Alice",
    "participant": "919xxx@s.whatsapp.net"  ← only in groups (actual sender)
  }
}
"""
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.core.security import verify_openwa_signature
from app.db.database import AsyncSessionLocal
from app.db.redis_client import check_idempotency, check_rate_limit, enqueue_message
from app.models.models import AuditLog, User
from app.services.security_service import security_service

router = APIRouter()
logger = get_logger(__name__)


def _extract_text(message: dict) -> str:
    """Pull plain text from an Evolution API message object."""
    return (
        message.get("conversation")
        or (message.get("extendedTextMessage") or {}).get("text")
        or (message.get("imageMessage") or {}).get("caption")
        or ""
    ).strip()


def _parse_evolution_event(payload: dict[str, Any]) -> dict[str, Any] | None:
    """Extract a normalised message dict, or None to skip."""
    # Evolution sends the event name as "messages.upsert"
    if payload.get("event") not in ("messages.upsert", "MESSAGES_UPSERT"):
        return None

    data = payload.get("data") or {}
    key = data.get("key") or {}

    if key.get("fromMe"):
        return None  # ignore messages sent by the bot

    remote_jid = key.get("remoteJid") or ""
    if not remote_jid:
        return None

    msg_obj = data.get("message") or {}
    body = _extract_text(msg_obj)
    if not body:
        return None

    is_group = "@g.us" in remote_jid
    participant = data.get("participant") or ""  # set only in groups

    if is_group:
        sender_jid = participant or remote_jid
        chat_id = remote_jid
    else:
        sender_jid = remote_jid
        chat_id = remote_jid

    sender_phone = (
        sender_jid
        .replace("@s.whatsapp.net", "")
        .replace("@c.us", "")
        .lstrip("+")
    )

    return {
        "sender_phone": sender_phone,
        "chat_id": chat_id,
        "is_group": is_group,
        "group_id": chat_id if is_group else "",
        "message_text": body,
        "message_id": key.get("id", ""),
        "timestamp": str(data.get("messageTimestamp", "")),
    }


async def _get_or_create_user(db: AsyncSession, wa_phone: str) -> User:
    result = await db.execute(select(User).where(User.wa_phone == wa_phone))
    user = result.scalar_one_or_none()
    if user:
        return user

    is_owner = wa_phone == settings.OWNER_WA_PHONE.lstrip("+")
    user = User(wa_phone=wa_phone, is_owner=is_owner, timezone=settings.TIMEZONE)
    db.add(user)
    await db.commit()
    await db.refresh(user)
    logger.info("Created user %s (owner=%s)", wa_phone, is_owner)
    return user


@router.post("/webhook/openwa")
async def evolution_webhook(request: Request) -> dict[str, str]:
    raw_body = await request.body()

    # Evolution API can sign webhooks with a secret — use same HMAC check
    signature = (
        request.headers.get("X-Evolution-Signature")
        or request.headers.get("x-evolution-signature")
    )
    if not verify_openwa_signature(raw_body, signature):
        logger.warning("Webhook signature verification failed")
        raise HTTPException(status_code=401, detail="Invalid signature")

    try:
        payload = await request.json()
    except Exception as exc:
        logger.error("Bad JSON in webhook: %s", exc)
        raise HTTPException(status_code=400, detail="Invalid JSON") from exc

    # Idempotency using the message ID
    idem_key = (
        payload.get("idempotencyKey")
        or (((payload.get("data") or {}).get("key") or {}).get("id"))
        or ""
    )
    if idem_key and not await check_idempotency(idem_key):
        return {"status": "duplicate"}

    parsed = _parse_evolution_event(payload)
    if parsed is None:
        return {"status": "ignored"}

    if not await check_rate_limit(parsed["sender_phone"]):
        logger.warning("Rate limit exceeded for %s", parsed["sender_phone"])
        return {"status": "rate_limited"}

    async with AsyncSessionLocal() as db:
        user = await _get_or_create_user(db, parsed["sender_phone"])
        await security_service.ensure_owner_allowed(db, settings.OWNER_WA_PHONE.lstrip("+"))
        effective_mode = await security_service.evaluate(
            db, parsed["chat_id"], parsed["sender_phone"], user
        )

    if effective_mode == "block":
        return {"status": "blocked"}

    if effective_mode == "silent_log":
        async with AsyncSessionLocal() as db:
            db.add(AuditLog(
                user_id=user.id,
                action="message.silent_log",
                details={
                    "chat_id": parsed["chat_id"],
                    "sender": parsed["sender_phone"],
                    "is_group": parsed["is_group"],
                    "preview": parsed["message_text"][:100],
                },
            ))
            await db.commit()
        return {"status": "silent_log"}

    stream_payload = {
        "user_id": user.id,
        "wa_phone": parsed["sender_phone"],
        "chat_id": parsed["chat_id"],
        "is_group": "true" if parsed["is_group"] else "false",
        "group_id": parsed["group_id"],
        "message_text": parsed["message_text"],
        "message_id": parsed["message_id"],
        "received_at": datetime.utcnow().isoformat(),
    }

    msg_id = await enqueue_message(stream_payload)
    logger.info(
        "Queued msg %s from %s (%s)",
        msg_id, parsed["sender_phone"], "group" if parsed["is_group"] else "dm",
    )
    return {"status": "queued", "stream_id": msg_id}
