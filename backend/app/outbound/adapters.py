"""Thin adapters over the three existing WhatsApp transports.

Each adapter contains no policy — it wraps one transport, normalises its
result into DeliveryResult, and contains its errors. Retry behaviour is
inherited from the underlying services (bridge_client already retries;
Evolution-path services return bool success).
"""
from __future__ import annotations

import logging

from app.outbound.base import DeliveryResult, WhatsAppOutbound

logger = logging.getLogger(__name__)


class PrimaryInstanceAdapter(WhatsAppOutbound):
    """Send via the primary Evolution API session."""

    async def send(self, chat_id: str, text: str, *, session_hint: str | None = None) -> DeliveryResult:
        from app.services.whatsapp_service import whatsapp_service

        ok = await whatsapp_service.send_text(chat_id, text)
        return DeliveryResult(ok=bool(ok), detail=None if ok else "primary send_text returned failure")


class AgentInstanceAdapter(WhatsAppOutbound):
    """Send via the dedicated agent Evolution session."""

    async def send(self, chat_id: str, text: str, *, session_hint: str | None = None) -> DeliveryResult:
        from app.services.agent_instance_service import agent_instance_service

        ok = await agent_instance_service.send_via_agent(chat_id, text)
        return DeliveryResult(ok=bool(ok), detail=None if ok else "agent send_via_agent returned failure")


class HermesBridgeAdapter(WhatsAppOutbound):
    """Send via the Hermes Baileys bridge (v3 path; has internal retries)."""

    async def send(self, chat_id: str, text: str, *, session_hint: str | None = None) -> DeliveryResult:
        from app.services.bridge_client import send_text as bridge_send_text

        ok = await bridge_send_text(chat_id, text)
        return DeliveryResult(ok=bool(ok), detail=None if ok else "bridge send_text returned failure")
