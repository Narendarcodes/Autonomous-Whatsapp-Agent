"""Agent Instance Service — manages the secondary Evolution API WhatsApp instance.

When the owner links a separate phone number as the "Agent Chat" interface, we create
a second Evolution API instance (AGENT_INSTANCE_NAME) for that number.  The AI's
outgoing replies are then routed through this instance so they appear to come from the
agent number rather than the owner's scanning number.

Flow:
  1. Owner enters agent phone number in Agent Identity tab
  2. We call create_agent_instance() → Evolution API creates the instance + returns QR
  3. QR is displayed in the dashboard AND forwarded to the owner's WhatsApp
  4. Owner opens their second phone → WhatsApp → Linked Devices → scans QR
  5. We poll get_agent_instance_status() every few seconds
  6. Once state == "open", we save bot_phone preference and whitelist the number
"""
import asyncio
import httpx

from app.core.config import settings
from app.core.logging import get_logger
from app.db.redis_client import cache_get, cache_set

logger = get_logger(__name__)

AGENT_INSTANCE_NAME = "agent-session"
AGENT_QR_CACHE_KEY = "whatsapp:agent_qr_code"
AGENT_PENDING_PHONE_KEY = "whatsapp:agent_pending_phone"
AGENT_STATE_CACHE_KEY = "whatsapp:agent_connection_state"
# The webhook URL for the agent instance re-uses the same backend webhook endpoint
# but with a separate path so we can distinguish owner vs agent events if needed.
AGENT_WEBHOOK_EVENTS = ["MESSAGES_UPSERT", "CONNECTION_UPDATE", "QRCODE_UPDATED"]


def _agent_webhook_url() -> str:
    base = settings.OPENWA_WEBHOOK_URL
    if base.endswith("/webhook/openwa"):
        return base.removesuffix("/webhook/openwa") + "/webhook/agent"
    return base.rstrip("/") + "/agent"


def _agent_qr_webhook_url() -> str:
    base = settings.OPENWA_WEBHOOK_URL
    if base.endswith("/webhook/openwa"):
        return base.removesuffix("/webhook/openwa") + "/webhook/agent-qr"
    return base.rstrip("/") + "/agent-qr"


class AgentInstanceService:
    """Manages the secondary Evolution API instance for the agent phone number."""

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

    def _instance_payload(self) -> dict:
        return {
            "instanceName": AGENT_INSTANCE_NAME,
            "integration": "WHATSAPP-BAILEYS",
            "qrcode": True,
            "webhook": {
                "url": _agent_webhook_url(),
                "byEvents": False,
                "base64": True,
                "events": AGENT_WEBHOOK_EVENTS,
            },
            "qrcodeWebhook": {"url": _agent_qr_webhook_url()},
        }

    async def create_agent_instance(self) -> str | None:
        """Create the agent Evolution API instance.
        
        Returns the QR code as a base64 string (data URI or raw base64),
        or None on failure.
        """
        client = await self._get_client()
        payload = self._instance_payload()
        try:
            resp = await client.post("/instance/create", json=payload)
            if resp.status_code in (200, 201):
                data = resp.json()
                qr = self._extract_qr(data)
                if qr:
                    await cache_set(AGENT_QR_CACHE_KEY, qr, ttl_seconds=300)
                    logger.info("Agent instance created, QR cached: %s", AGENT_INSTANCE_NAME)
                return qr
            if resp.status_code == 403:
                # Instance already exists — try to fetch the QR
                logger.info("Agent instance already exists, fetching QR")
                return await self.get_agent_qr()
            logger.error("create_agent_instance failed %s: %s", resp.status_code, resp.text[:300])
        except httpx.HTTPError as exc:
            logger.error("create_agent_instance HTTP error: %s", exc)
        return None

    async def get_agent_qr(self) -> str | None:
        """Fetch the current QR code for the agent instance from Evolution API."""
        # First try cache
        cached = await cache_get(AGENT_QR_CACHE_KEY)
        if cached:
            return cached
        
        client = await self._get_client()
        try:
            resp = await client.get(f"/instance/qrcode/{AGENT_INSTANCE_NAME}?image=true")
            if resp.status_code == 200:
                data = resp.json()
                qr = self._extract_qr(data)
                if qr:
                    await cache_set(AGENT_QR_CACHE_KEY, qr, ttl_seconds=300)
                    return qr
        except httpx.HTTPError as exc:
            logger.error("get_agent_qr error: %s", exc)
        return None

    async def get_agent_instance_status(self) -> dict:
        """Return the current connection state of the agent instance.
        
        Returns dict with at minimum:
          - state: "open" | "connecting" | "close" | "unknown"
          - qr: base64 QR code (if state is not "open")
        """
        client = await self._get_client()
        try:
            resp = await client.get(f"/instance/connectionState/{AGENT_INSTANCE_NAME}")
            if resp.status_code == 200:
                data = resp.json()
                state = data.get("instance", {}).get("state", "unknown")
                result = {"state": state}
                if state != "open":
                    # Try to get a fresh QR
                    qr = await self.get_agent_qr()
                    result["qr"] = qr or ""
                return result
        except httpx.HTTPError as exc:
            logger.error("get_agent_instance_status error: %s", exc)
        return {"state": "unknown", "qr": ""}

    async def get_agent_phone(self) -> str | None:
        """Return the phone number linked to the agent instance (once connected)."""
        client = await self._get_client()
        try:
            resp = await client.get("/instance/fetchInstances")
            if resp.status_code == 200:
                instances = resp.json()
                for inst in instances:
                    if inst.get("name") == AGENT_INSTANCE_NAME:
                        owner_jid = inst.get("ownerJid")
                        if owner_jid:
                            return owner_jid.split("@")[0].split(":")[0].lstrip("+")
        except Exception as exc:
            logger.error("get_agent_phone error: %s", exc)
        return None

    async def send_via_agent(self, to: str, message: str) -> bool:
        """Send a text message through the agent instance (appears from agent number)."""
        from app.services.whatsapp_service import WhatsAppService
        number = WhatsAppService.to_chat_id(to)
        if not number:
            logger.warning("send_via_agent called with empty target")
            return False

        client = await self._get_client()
        url = f"/message/sendText/{AGENT_INSTANCE_NAME}"
        payload = {"number": number, "text": message}
        try:
            resp = await client.post(url, json=payload)
            if resp.status_code in (200, 201):
                logger.info("Sent agent message to %s via %s", number, AGENT_INSTANCE_NAME)
                return True
            logger.warning("send_via_agent failed %s: %s", resp.status_code, resp.text[:200])
        except httpx.HTTPError as exc:
            logger.error("send_via_agent error: %s", exc)
        return False

    async def configure_agent_webhook(self) -> bool:
        """Force the existing agent Evolution instance to use the current webhook config."""
        client = await self._get_client()
        payload = {
            "webhook": {
                "enabled": True,
                "url": _agent_webhook_url(),
                "byEvents": False,
                "base64": True,
                "events": AGENT_WEBHOOK_EVENTS,
            }
        }
        try:
            resp = await client.post(f"/webhook/set/{AGENT_INSTANCE_NAME}", json=payload)
            if resp.status_code in (200, 201):
                logger.info("Evolution API webhook configured for %s", AGENT_INSTANCE_NAME)
                return True
            logger.warning("Agent webhook configure failed %s: %s", resp.status_code, resp.text[:200])
        except httpx.HTTPError as exc:
            logger.error("configure_agent_webhook error: %s", exc)
        return False

    async def delete_agent_instance(self) -> bool:
        """Delete the agent instance (disconnect session)."""
        client = await self._get_client()
        try:
            resp = await client.delete(f"/instance/delete/{AGENT_INSTANCE_NAME}")
            if resp.status_code in (200, 201):
                await cache_set(AGENT_QR_CACHE_KEY, "", ttl_seconds=1)
                await cache_set(AGENT_STATE_CACHE_KEY, "disconnected", ttl_seconds=1)
                logger.info("Agent instance deleted: %s", AGENT_INSTANCE_NAME)
                return True
            logger.error("delete_agent_instance failed %s: %s", resp.status_code, resp.text[:200])
        except Exception as exc:
            logger.error("delete_agent_instance error: %s", exc)
        return False

    @staticmethod
    def _extract_qr(data) -> str:
        """Recursively search for a base64 QR code string in Evolution API response."""
        if isinstance(data, str):
            return data if ("base64" in data or len(data) > 100) else ""
        if isinstance(data, dict):
            for key in ("base64", "qrcode", "qr", "code"):
                found = AgentInstanceService._extract_qr(data.get(key))
                if found:
                    return found
            for child in data.values():
                found = AgentInstanceService._extract_qr(child)
                if found:
                    return found
        return ""


# Singleton instance
agent_instance_service = AgentInstanceService()
