"""WhatsApp service — sends messages via OpenWA REST API.

OpenWA endpoints used:
    POST /sessions/{id}/messages/send-text   - send DM or group message
    GET  /sessions/{id}                       - session status
    POST /sessions/{id}/webhooks             - register webhook URL
    GET  /sessions/{id}/qr                    - QR code image
"""
import asyncio

import httpx

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class WhatsAppService:
    def __init__(self) -> None:
        self.base_url = settings.OPENWA_BASE_URL.rstrip("/")
        self.session_id = settings.OPENWA_SESSION_ID
        self.api_key = settings.OPENWA_API_KEY
        self._client: httpx.AsyncClient | None = None

    async def _client_get(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(
                    connect=settings.HTTP_CONNECT_TIMEOUT,
                    read=settings.HTTP_READ_TIMEOUT,
                    write=settings.HTTP_WRITE_TIMEOUT,
                    pool=settings.HTTP_POOL_TIMEOUT,
                ),
                headers={"X-Api-Key": self.api_key, "Content-Type": "application/json"},
            )
        return self._client

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    @staticmethod
    def to_chat_id(target: str) -> str:
        """Normalize a phone or JID to OpenWA chatId form.

        Accepts:
            "919999999999"             → "919999999999@c.us"
            "+91-9999-999999"          → "919999999999@c.us"
            "123456789-123456@g.us"   → unchanged (group)
            "919999999999@c.us"        → unchanged
        """
        target = (target or "").strip()
        if target.endswith("@c.us") or target.endswith("@g.us"):
            return target
        cleaned = "".join(ch for ch in target if ch.isdigit())
        return f"{cleaned}@c.us" if cleaned else ""

    async def send_text(self, to: str, message: str) -> bool:
        """Send a text message. `to` may be a phone or a group JID."""
        chat_id = self.to_chat_id(to)
        if not chat_id:
            logger.warning("send_text called with empty target")
            return False

        url = f"{self.base_url}/sessions/{self.session_id}/messages/send-text"
        payload = {"chatId": chat_id, "text": message}

        client = await self._client_get()
        for attempt in range(settings.RETRY_MAX_ATTEMPTS):
            try:
                resp = await client.post(url, json=payload)
                if resp.status_code in (200, 201):
                    logger.info("Sent message to %s", chat_id)
                    return True
                logger.warning(
                    "OpenWA send_text returned %s: %s", resp.status_code, resp.text[:200]
                )
            except httpx.HTTPError as exc:
                logger.warning("send_text attempt %d failed: %s", attempt + 1, exc)
            await asyncio.sleep(min(settings.RETRY_BASE_DELAY * (2**attempt), settings.RETRY_MAX_DELAY))
        return False

    async def session_status(self) -> dict | None:
        client = await self._client_get()
        try:
            resp = await client.get(f"{self.base_url}/sessions/{self.session_id}")
            if resp.status_code == 200:
                return resp.json()
        except httpx.HTTPError as exc:
            logger.error("session_status error: %s", exc)
        return None

    async def register_webhook(self) -> bool:
        """Tell OpenWA to deliver events to our FastAPI backend."""
        client = await self._client_get()
        payload = {
            "url": settings.OPENWA_WEBHOOK_URL,
            "events": ["message.received", "session.status"],
            "secret": settings.OPENWA_WEBHOOK_SECRET,
        }
        try:
            resp = await client.post(
                f"{self.base_url}/sessions/{self.session_id}/webhooks", json=payload
            )
            if resp.status_code in (200, 201):
                logger.info("Webhook registered with OpenWA: %s", settings.OPENWA_WEBHOOK_URL)
                return True
            logger.error("Webhook registration failed %s: %s", resp.status_code, resp.text[:200])
        except httpx.HTTPError as exc:
            logger.error("register_webhook error: %s", exc)
        return False


whatsapp_service = WhatsAppService()
