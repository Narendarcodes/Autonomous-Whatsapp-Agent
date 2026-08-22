"""Dashboard API — WhatsApp pairing status + live QR from the Hermes bridge.

Mounted under /api/pairing (dashboard-auth protected). The frontend polls
/status until qr_available, renders the QR from /qr, and keeps polling
until paired=true.
"""
from fastapi import APIRouter, Depends, HTTPException

from app.api.setup import verify_api_admin
from app.services.whatsapp_pairing_service import pairing_service

router = APIRouter(dependencies=[Depends(verify_api_admin)])


@router.get("/status")
async def pairing_status() -> dict:
    """Pairing snapshot WITHOUT the QR payload (cheap poll)."""
    return await pairing_service.get_pairing_state()


@router.get("/qr")
async def pairing_qr() -> dict:
    """Latest complete QR block from the bridge log (unicode ▄▀█ art)."""
    qr, captured_at = pairing_service.read_latest_qr()
    if not qr:
        raise HTTPException(status_code=404, detail="No QR available yet")
    return {"format": "unicode_blocks", "qr": qr, "captured_at": captured_at}
