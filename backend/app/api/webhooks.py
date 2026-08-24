"""Evolution API webhook receiver — thin transport edge (ADR-0007).

Everything that remains here is an HTTP/transport concern:
  - HMAC signature verification over the raw body
  - QR / connection-update side events
  - payload normalization via the intake edge adapter (pure)
  - admitting the trusted InboundMessage into the Inbox; Ack -> HTTP mapping

Message gating, queueing, policy, and dispatch live behind the seam in
app/intake/ — see ADR-0007 and docs/adr/0007-message-intake-module.md.
"""
import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from app.core.config import settings
from app.core.logging import get_logger
from app.core.security import verify_openwa_signature
from app.intake.runtime import get_inbox
from app.intake.types import Ack, InboundMessage

router = APIRouter()
logger = get_logger(__name__)


def _event_name(payload: dict[str, Any]) -> str:
    return str(payload.get("event") or "").lower().replace("_", ".")


async def _parse_event(payload: dict[str, Any]) -> InboundMessage | None:
    """Edge IO (bot identity + mode resolution), then pure normalization."""
    if _event_name(payload) not in ("messages.upsert", "send.message"):
        return None

    instance = payload.get("instance")
    if instance == "agent-session":
        bot_phone = await _bot_phone_cached("whatsapp:agent_bot_phone", agent=True)
    else:
        bot_phone = await _bot_phone_cached("whatsapp:bot_phone", agent=False)

    from app.services.preferences_service import preferences_service

    bot_mode = await preferences_service.get_owner_preference(
        "bot_mode", settings.BOT_RELATIONSHIP_MODE
    )

    from app.intake.evolution import normalize_event

    return normalize_event(payload, bot_phone=bot_phone, bot_mode=bot_mode)


async def _bot_phone_cached(cache_key: str, *, agent: bool) -> str | None:
    from app.db.redis_client import cache_get, cache_set

    bot_phone = await cache_get(cache_key)
    if bot_phone:
        return bot_phone
    if agent:
        from app.services.agent_instance_service import agent_instance_service

        bot_phone = await agent_instance_service.get_agent_phone()
    else:
        from app.services.whatsapp_service import whatsapp_service

        bot_phone = await whatsapp_service.get_bot_phone()
    if bot_phone:
        await cache_set(cache_key, bot_phone, ttl_seconds=86400)
    return bot_phone


# ------------------------------------------------------------ QR handling


def _extract_qr(value: Any) -> str:
    if isinstance(value, str):
        return value if ("base64" in value or len(value) > 100) else ""
    if isinstance(value, dict):
        for key in ("base64", "qrcode", "qr", "code"):
            found = _extract_qr(value.get(key))
            if found:
                return found
        for child in value.values():
            found = _extract_qr(child)
            if found:
                return found
    return ""


async def _store_qr(payload: dict[str, Any], cache_key_getter) -> bool:
    qr_data = _extract_qr(payload.get("data") or payload)
    if not qr_data:
        return False
    from app.db.redis_client import cache_set

    await cache_set(cache_key_getter(), qr_data, ttl_seconds=180)
    logger.info("QR code cached (180s TTL)")
    return True


@router.post("/webhook/qr")
async def evolution_qr_webhook(request: Request) -> dict[str, str]:
    """Dedicated QR code update webhook."""
    try:
        payload = await request.json()
    except Exception:
        return {"status": "bad_json"}
    ok = await _store_qr(payload, lambda: "whatsapp:qr_code")
    return {"status": "ok" if ok else "no_qr"}


@router.post("/webhook/agent-qr")
async def agent_qr_webhook(request: Request) -> dict[str, str]:
    """Dedicated QR code update webhook for the agent instance."""
    try:
        payload = await request.json()
    except Exception:
        return {"status": "bad_json"}

    def key() -> str:
        from app.services.agent_instance_service import AGENT_QR_CACHE_KEY

        return AGENT_QR_CACHE_KEY

    ok = await _store_qr(payload, key)
    return {"status": "ok" if ok else "no_qr"}


# ------------------------------------------------------------- main webhook


@router.post("/webhook/openwa")
async def evolution_webhook(request: Request) -> dict[str, str]:
    raw_body = await request.body()

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
        logger.error(f"Bad JSON in webhook: {exc}")
        raise HTTPException(status_code=400, detail="Invalid JSON") from exc

    event = _event_name(payload)
    if event == "qrcode.updated":
        ok = await _store_qr(payload, lambda: "whatsapp:qr_code")
        return {"status": "qr_updated" if ok else "no_qr"}
    if event == "connection.update":
        state = (payload.get("data") or {}).get("state") or (payload.get("data") or {}).get("status")
        if state:
            from app.db.redis_client import cache_set

            await cache_set("whatsapp:connection_state", str(state), ttl_seconds=300)
        return {"status": "connection_update", "state": str(state or "")}

    # Edge adapter: Evolution wire shape -> trusted InboundMessage
    message = await _parse_event(payload)
    if message is None:
        return {"status": "ignored", "reason": "not_parseable_or_from_me"}

    # THE seam: admission + durable queueing happen behind the Inbox interface
    ack = await get_inbox().accept(message)

    if ack in (Ack.RATE_LIMITED, Ack.REJECTED_QUEUE_FULL):
        _maybe_alert_owner(message, ack)

    message_id = message.message_id or ""
    if ack is Ack.ACCEPTED:
        return {"status": "queued", "message_id": message_id}
    return {"status": ack.value, "message_id": message_id}


@router.post("/webhook/agent")
async def agent_webhook(request: Request) -> dict[str, str]:
    """Main webhook for the agent WhatsApp instance (same pipeline)."""
    return await evolution_webhook(request)


# --------------------------------------------------------- owner alerts


def _maybe_alert_owner(message: InboundMessage, ack: Ack) -> None:
    """Best-effort owner heads-up when their messages are shed.

    Fire-and-forget on purpose: alerts must never delay the webhook response
    nor crash admission. Owner comparison uses the boot-synced setting.
    """

    async def _send() -> None:
        try:
            if message.sender_phone != settings.OWNER_WA_PHONE.lstrip("+"):
                return
            if ack is Ack.RATE_LIMITED:
                text = (
                    "⚠️ *System Alert*: You are sending messages too quickly. "
                    "Please wait a moment before sending more messages."
                )
            else:
                text = (
                    "⚠️ *System Alert*: You are sending too many messages. "
                    "Some messages may be skipped to prevent overload."
                )
            if message.instance == "agent-session":
                from app.services.agent_instance_service import agent_instance_service

                await agent_instance_service.send_via_agent(message.sender_phone, text)
            else:
                from app.services.whatsapp_service import whatsapp_service

                await whatsapp_service.send_text(message.sender_phone, text)
        except Exception as exc:  # noqa: BLE001 - alerting must never throw
            logger.warning("Owner alert failed: %s", exc)

    asyncio.create_task(_send())
