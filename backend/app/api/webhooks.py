"""OpenWA webhook receiver.

OpenWA event payload shape (message.received):
{
  "event": "message.received",
  "sessionId": "my-session",
  "idempotencyKey": "uuid-v4",
  "data": {
    "id": {"_serialized": "true_628xxx@c.us_ABCD..."},
    "body": "Hello",
    "type": "chat",
    "from": "628xxx@c.us"  (DM)  OR  "1234-5678@g.us" (group),
    "author": "628xxx@c.us"  (ONLY for groups, the human sender),
    "fromMe": false,
    "timestamp": 1700000000,
    "hasMedia": false
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


def _parse_openwa_event(payload: dict[str, Any]) -> dict[str, Any] | None:
    """Extract a normalized message dict, or None if this event should be skipped."""
    if payload.get("event") != "message.received":
        return None

    data = payload.get("data") or {}
    if data.get("fromMe"):
        return None  # don't react to our own messages
    if data.get("type") != "chat":
        return None  # ignore media, stickers, system events
    if data.get("isStatus"):
        return None  # ignore status broadcasts

    body = (data.get("body") or "").strip()
    if not body:
        return None

    from_jid = data.get("from") or ""
    author_jid = data.get("author") or ""
    is_group = "@g.us" in from_jid

    if is_group:
        sender_jid = author_jid or from_jid
        sender_phone = sender_jid.replace("@c.us", "").replace("@s.whatsapp.net", "").lstrip("+")
        chat_id = from_jid  # the group JID — we'll reply here
    else:
        sender_phone = from_jid.replace("@c.us", "").replace("@s.whatsapp.net", "").lstrip("+")
        chat_id = from_jid

    return {
        "sender_phone": sender_phone,
        "chat_id": chat_id,
        "is_group": is_group,
        "group_id": chat_id if is_group else "",
        "message_text": body,
        "message_id": (data.get("id") or {}).get("_serialized", ""),
        "timestamp": str(data.get("timestamp", "")),
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
async def openwa_webhook(request: Request) -> dict[str, str]:
    raw_body = await request.body()
    signature = request.headers.get("X-OpenWA-Signature") or request.headers.get("x-openwa-signature")

    if not verify_openwa_signature(raw_body, signature):
        logger.warning("Webhook signature verification failed")
        raise HTTPException(status_code=401, detail="Invalid signature")

    try:
        payload = await request.json()
    except Exception as exc:
        logger.error("Bad JSON in webhook: %s", exc)
        raise HTTPException(status_code=400, detail="Invalid JSON") from exc

    # Idempotency — OpenWA may retry on transient failures
    idem_key = payload.get("idempotencyKey") or payload.get("deliveryId") or ""
    if idem_key and not await check_idempotency(idem_key):
        logger.info("Duplicate webhook %s — skipping", idem_key)
        return {"status": "duplicate"}

    parsed = _parse_openwa_event(payload)
    if parsed is None:
        return {"status": "ignored"}

    # Rate limit per sender
    if not await check_rate_limit(parsed["sender_phone"]):
        logger.warning("Rate limit exceeded for %s", parsed["sender_phone"])
        return {"status": "rate_limited"}

    # Look up or create user (the human sender — not the group)
    async with AsyncSessionLocal() as db:
        user = await _get_or_create_user(db, parsed["sender_phone"])
        # Ensure owner's DM is in allow_all on first run
        await security_service.ensure_owner_allowed(db, settings.OWNER_WA_PHONE.lstrip("+"))
        # ACL check — happens before anything hits Redis
        effective_mode = await security_service.evaluate(
            db, parsed["chat_id"], parsed["sender_phone"], user
        )

    if effective_mode == "block":
        logger.debug("Blocked message from %s in %s", parsed["sender_phone"], parsed["chat_id"])
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
