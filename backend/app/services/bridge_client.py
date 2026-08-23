"""Hermes Baileys bridge HTTP client — the v3 WhatsApp outbound transport.

The Hermes gateway (port 8642) hosts a small Baileys bridge alongside its
OpenAI-compatible API:
  - POST /send   {chatId, message}  → deliver a WhatsApp message
  - GET  /health                   → {status: "connected" | ...}

This module replaces the old Evolution API client for backend-initiated
sends (approval notices, setup prompts, alerts). Agent *replies* are
delivered by the bridge itself from Hermes sessions; this client is only
for messages the backend originates directly.
"""
import asyncio

import httpx

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

_client: httpx.AsyncClient | None = None


def _base_url() -> str:
    return settings.HERMES_BASE_URL.rstrip("/")


async def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(
            base_url=_base_url(),
            timeout=httpx.Timeout(
                connect=settings.HTTP_CONNECT_TIMEOUT,
                read=settings.HTTP_READ_TIMEOUT,
                write=settings.HTTP_WRITE_TIMEOUT,
                pool=settings.HTTP_POOL_TIMEOUT,
            ),
        )
    return _client


async def close() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


async def send_text(chat_id: str, message: str) -> bool:
    """Deliver a plain-text WhatsApp message via the bridge POST /send.

    chatId is a bare phone number ("919999999999") or a JID
    ("...@s.whatsapp.net" / "...@g.us") — passed through untouched.
    """
    chat_id = (chat_id or "").strip()
    if not chat_id or not (message or "").strip():
        logger.warning("bridge send_text called with empty target or body")
        return False

    client = await _get_client()
    payload = {"chatId": chat_id, "message": message}
    for attempt in range(settings.RETRY_MAX_ATTEMPTS):
        try:
            resp = await client.post("/send", json=payload)
            if resp.status_code == 200:
                logger.info("Bridge delivered message to %s", chat_id)
                return True
            logger.warning(
                "Bridge send_text %s → %s: %s",
                chat_id, resp.status_code, resp.text[:200],
            )
        except httpx.HTTPError as exc:
            logger.warning("bridge send_text attempt %d failed: %s", attempt + 1, exc)
        await asyncio.sleep(
            min(settings.RETRY_BASE_DELAY * (2 ** attempt), settings.RETRY_MAX_DELAY)
        )
    return False


async def bridge_status() -> str:
    """Return the bridge's WhatsApp connection state.

    "connected" when live; otherwise an "http_..." or "error: ..." sentinel.
    """
    client = await _get_client()
    try:
        resp = await client.get("/health")
        if resp.status_code == 200:
            return str(resp.json().get("status") or "unknown")
        return f"http_{resp.status_code}"
    except httpx.HTTPError as exc:
        return f"error: {exc}"
