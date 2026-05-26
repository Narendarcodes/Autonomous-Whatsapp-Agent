"""WhatsApp service — sends messages via Evolution API.

Evolution API (atendai/evolution-api) is a Baileys-based WhatsApp REST
server. It replaces the original openwa/openwa image (which had no
public Docker image) while keeping the same overall architecture:
  - QR-code authenticated personal number session
  - Webhooks for inbound messages
  - REST API for outbound messages

Evolution API docs: https://doc.evolution-api.com/

Key differences from the original openwa REST contract:
  - Base URL: http://openwa:8080   (our compose maps port 2785 → 8080)
  - Auth header: apikey (not X-Api-Key)
  - Session concept: "instance" (created once, persists across restarts)
  - Send endpoint: POST /message/sendText/{instance}
  - Webhook events: { event: "messages.upsert", data: { ... } }
"""
import asyncio

import httpx

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

# The Evolution API instance name — created once during setup, reused forever.
INSTANCE_NAME = settings.OPENWA_SESSION_ID  # "my-session"


class WhatsAppService:
    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None

    def _headers(self) -> dict:
        return {"apikey": settings.OPENWA_API_KEY, "Content-Type": "application/json"}

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=settings.OPENWA_BASE_URL.rstrip("/"),
                timeout=httpx.Timeout(
                    connect=settings.HTTP_CONNECT_TIMEOUT,
                    read=settings.HTTP_READ_TIMEOUT,
                    write=settings.HTTP_WRITE_TIMEOUT,
                    pool=settings.HTTP_POOL_TIMEOUT,
                ),
                headers=self._headers(),
            )
        return self._client

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    @staticmethod
    def to_chat_id(target: str) -> str:
        """Normalise a phone or JID to Evolution API format.

        Evolution API uses the same WhatsApp JID format:
          personal: "919999999999@s.whatsapp.net"
          group:    "123456789-123456@g.us"
        """
        target = (target or "").strip()
        if target.endswith("@g.us"):
            return target
        if target.endswith("@s.whatsapp.net"):
            return target
        cleaned = "".join(ch for ch in target if ch.isdigit())
        return f"{cleaned}@s.whatsapp.net" if cleaned else ""

    async def send_text(self, to: str, message: str) -> bool:
        """Send a plain-text message to a phone or group JID."""
        number = self.to_chat_id(to)
        if not number:
            logger.warning("send_text called with empty target")
            return False

        url = f"/message/sendText/{INSTANCE_NAME}"
        payload = {"number": number, "text": message}

        client = await self._get_client()
        for attempt in range(settings.RETRY_MAX_ATTEMPTS):
            try:
                resp = await client.post(url, json=payload)
                if resp.status_code in (200, 201):
                    logger.info("Sent message to %s", number)
                    return True
                logger.warning(
                    "Evolution API send_text %s → %s: %s",
                    number, resp.status_code, resp.text[:200],
                )
            except httpx.HTTPError as exc:
                logger.warning("send_text attempt %d failed: %s", attempt + 1, exc)
            await asyncio.sleep(
                min(settings.RETRY_BASE_DELAY * (2 ** attempt), settings.RETRY_MAX_DELAY)
            )
        return False

    async def instance_status(self) -> dict | None:
        """Return the current instance connection state."""
        client = await self._get_client()
        try:
            resp = await client.get(f"/instance/connectionState/{INSTANCE_NAME}")
            if resp.status_code == 200:
                return resp.json()
        except httpx.HTTPError as exc:
            logger.error("instance_status error: %s", exc)
        return None

    async def create_instance(self) -> bool:
        """Create the WhatsApp instance (only needed once on first boot)."""
        client = await self._get_client()
        payload = {
            "instanceName": INSTANCE_NAME,
            "integration": "WHATSAPP-BAILEYS",
            "qrcode": True,
            "webhook": {
                "url": settings.OPENWA_WEBHOOK_URL,
                "byEvents": True,
                "base64": False,
                "events": ["MESSAGES_UPSERT", "CONNECTION_UPDATE"],
            },
        }
        try:
            resp = await client.post("/instance/create", json=payload)
            if resp.status_code in (200, 201):
                logger.info("Evolution API instance created: %s", INSTANCE_NAME)
                return True
            if resp.status_code == 403:
                logger.info("Instance already exists: %s", INSTANCE_NAME)
                return True
            logger.error(
                "Instance create failed %s: %s", resp.status_code, resp.text[:200]
            )
        except httpx.HTTPError as exc:
            logger.error("create_instance error: %s", exc)
        return False

    async def get_qr_code(self) -> bytes | None:
        """Return the raw QR code image bytes for the setup flow."""
        client = await self._get_client()
        try:
            resp = await client.get(f"/instance/qrcode/{INSTANCE_NAME}")
            if resp.status_code == 200:
                data = resp.json()
                # Evolution returns QR as base64 or as a raw endpoint
                qr_url = data.get("qrcode") or data.get("qr")
                if qr_url and qr_url.startswith("http"):
                    img_resp = await client.get(qr_url)
                    return img_resp.content
        except httpx.HTTPError as exc:
            logger.error("get_qr_code error: %s", exc)
        return None


whatsapp_service = WhatsAppService()
