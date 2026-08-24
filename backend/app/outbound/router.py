"""OutboundRouter — the ONE place that knows which transport sends a reply.

Consolidates the session-selection rules previously copy-pasted across
webhooks.py, stages.py, whatsapp_service and agent_harness:

    1. inbound hint "agent-session"      -> agent instance adapter
    2. owner bot_mode == "dual_number"   -> agent instance adapter
    3. otherwise                         -> primary instance adapter

System notifications that deliberately target the Hermes bridge regardless
of session (permission/setup notices via bridge_client) stay on their
explicit dependency by design — they are policy-chosen targets, not routed
replies.

Never raises: delivery failures are DeliveryResult values (ADR-0007 spirit).
"""
from __future__ import annotations

import logging
from typing import Awaitable, Callable

from app.outbound.base import DeliveryResult, WhatsAppOutbound
from app.outbound.adapters import AgentInstanceAdapter, PrimaryInstanceAdapter

logger = logging.getLogger(__name__)

ModeResolver = Callable[[], Awaitable[str]]


class OutboundRouter(WhatsAppOutbound):
    def __init__(
        self,
        *,
        primary: WhatsAppOutbound | None = None,
        agent: WhatsAppOutbound | None = None,
        mode_resolver: ModeResolver | None = None,
    ) -> None:
        self._primary = primary or PrimaryInstanceAdapter()
        self._agent = agent or AgentInstanceAdapter()
        self._mode_resolver = mode_resolver or self._default_mode_resolver

    @staticmethod
    async def _default_mode_resolver() -> str:
        from app.core.config import settings
        from app.services.preferences_service import preferences_service

        return (
            await preferences_service.get_owner_preference("bot_mode", settings.BOT_RELATIONSHIP_MODE)
            or "self_chat"
        )

    async def send(self, chat_id: str, text: str, *, session_hint: str | None = None) -> DeliveryResult:
        if not (chat_id or "").strip() or not (text or "").strip():
            return DeliveryResult(ok=False, detail="empty chat_id or text")

        adapter: WhatsAppOutbound
        if session_hint == "agent-session":
            adapter = self._agent
        else:
            bot_mode = await self._mode_resolver()
            adapter = self._agent if bot_mode == "dual_number" else self._primary

        try:
            result = await adapter.send(chat_id, text, session_hint=session_hint)
        except Exception as exc:  # noqa: BLE001 - failures are values at this seam
            logger.error("Outbound %s delivery to %s raised: %s", type(adapter).__name__, chat_id, exc)
            return DeliveryResult(ok=False, detail=f"{type(adapter).__name__} raised {type(exc).__name__}")

        if not result:
            logger.warning("Outbound delivery failed via %s to %s: %s",
                           type(adapter).__name__, chat_id, result.detail)
        else:
            logger.info("Outbound delivered via %s to %s", type(adapter).__name__, chat_id)
        return result


_router: OutboundRouter | None = None


def get_outbound() -> OutboundRouter:
    """Composition root singleton for production call sites."""
    global _router
    if _router is None:
        _router = OutboundRouter()
    return _router


def reset_for_tests() -> None:
    global _router
    _router = None
