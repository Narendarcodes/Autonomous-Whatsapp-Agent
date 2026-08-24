"""Session-policy pre-gates for the production Inbox (ADR-0007 slice 5 prep).

These run INSIDE accept(), before the frozen admission gates, and encode
the legacy router's mode/session rules:

  - dual_number mode: only agent-session events are processed
  - agent-session: only the owner's chat may pass

Returns Ack.IGNORED when policy says skip, None to continue into admission.
"""
from __future__ import annotations

import logging
from typing import Awaitable, Callable

from sqlalchemy import select

from app.core.config import settings
from app.intake.types import Ack, InboundMessage
from app.models.models import User

logger = logging.getLogger(__name__)


async def _resolve_owner_phone() -> str:
    """cache (5 min) -> DB -> settings fallback. Same as legacy router."""
    from app.db.database import AsyncSessionLocal
    from app.db.redis_client import cache_get, cache_set

    owner_phone = await cache_get("whatsapp:owner_phone")
    if owner_phone:
        return owner_phone
    try:
        async with AsyncSessionLocal() as db:
            result = await db.execute(select(User).where(User.is_owner == True))  # noqa: E712
            owner_user = result.scalar_one_or_none()
            if owner_user and owner_user.wa_phone:
                await cache_set("whatsapp:owner_phone", owner_user.wa_phone, ttl_seconds=300)
                return owner_user.wa_phone
    except Exception as e:  # noqa: BLE001 - parity with legacy fallback path
        logger.error("Failed to fetch owner from database: %s", e)
    return settings.OWNER_WA_PHONE.lstrip("+")


async def evolution_session_policy(message: InboundMessage) -> Ack | None:
    if message.bot_mode == "dual_number" and message.instance != "agent-session":
        logger.info("Policy: ignoring primary-session event in dual_number mode")
        return Ack.IGNORED

    if message.instance == "agent-session":
        chat_clean = message.chat_id.split("@")[0].split(":")[0].lstrip("+")
        if chat_clean != await _resolve_owner_phone():
            logger.info("Policy: agent-session event targets non-owner chat %s", message.chat_id)
            return Ack.IGNORED

    return None


PolicyFn = Callable[[InboundMessage], Awaitable[Ack | None]]
