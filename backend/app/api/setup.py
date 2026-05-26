"""Evolution API setup endpoints — instance creation, QR display."""
import httpx
from fastapi import APIRouter, HTTPException, Response

from app.core.config import settings
from app.core.logging import get_logger
from app.services.whatsapp_service import INSTANCE_NAME, whatsapp_service

router = APIRouter()
logger = get_logger(__name__)


@router.get("/setup/qr-status")
async def qr_status() -> dict:
    info = await whatsapp_service.instance_status()
    if info is None:
        raise HTTPException(status_code=503, detail="Evolution API unreachable")
    return {
        "state": info.get("instance", {}).get("state"),
        "instance": INSTANCE_NAME,
        "qr_url": f"{settings.BASE_URL}/setup/qr-image",
    }


@router.get("/setup/qr-image")
async def qr_image() -> Response:
    url = f"{settings.OPENWA_BASE_URL}/instance/qrcode/{INSTANCE_NAME}"
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(url, headers={"apikey": settings.OPENWA_API_KEY})
    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail="QR image not available yet")
    # Evolution returns JSON with base64 qr; proxy the raw endpoint
    data = resp.json()
    img_url = data.get("qrcode") or data.get("qr") or ""
    if img_url.startswith("data:image"):
        # base64 inline — decode and return
        import base64
        _, encoded = img_url.split(",", 1)
        return Response(content=base64.b64decode(encoded), media_type="image/png")
    raise HTTPException(status_code=502, detail="Unexpected QR format")


@router.post("/setup/create-instance")
async def create_instance() -> dict:
    ok = await whatsapp_service.create_instance()
    if not ok:
        raise HTTPException(status_code=502, detail="Instance creation failed")
    return {"status": "created", "instance": INSTANCE_NAME}
