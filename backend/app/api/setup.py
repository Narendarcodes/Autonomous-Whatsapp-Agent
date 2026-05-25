"""OpenWA setup endpoints — QR display and webhook registration."""
import httpx
from fastapi import APIRouter, HTTPException, Response

from app.core.config import settings
from app.core.logging import get_logger
from app.services.whatsapp_service import whatsapp_service

router = APIRouter()
logger = get_logger(__name__)


@router.get("/setup/qr-status")
async def qr_status() -> dict:
    info = await whatsapp_service.session_status()
    if info is None:
        raise HTTPException(status_code=503, detail="OpenWA unreachable")
    return {
        "status": info.get("status"),
        "session_id": settings.OPENWA_SESSION_ID,
        "qr_url": f"{settings.BASE_URL}/setup/qr-image",
    }


@router.get("/setup/qr-image")
async def qr_image() -> Response:
    url = f"{settings.OPENWA_BASE_URL}/sessions/{settings.OPENWA_SESSION_ID}/qr"
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(url, headers={"X-Api-Key": settings.OPENWA_API_KEY})
    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail="QR image not available yet")
    return Response(content=resp.content, media_type=resp.headers.get("content-type", "image/png"))


@router.post("/setup/register-webhook")
async def register_webhook() -> dict:
    ok = await whatsapp_service.register_webhook()
    if not ok:
        raise HTTPException(status_code=502, detail="Webhook registration failed")
    return {"status": "registered", "url": settings.OPENWA_WEBHOOK_URL}
