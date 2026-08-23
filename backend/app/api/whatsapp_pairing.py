"""Dashboard API — WhatsApp pairing status + live QR from the Hermes bridge.

Mounted under /api/pairing (dashboard-auth protected). The frontend polls
/status until qr_available, renders the QR from /qr, and keeps polling
until paired=true.
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.api.setup import verify_api_admin
from app.services.bridge_config_service import (
    get_bridge_config,
    set_bridge_config,
)
from app.services.whatsapp_pairing_service import pairing_service

router = APIRouter(dependencies=[Depends(verify_api_admin)])


class BridgeConfigPayload(BaseModel):
    """Runtime WhatsApp bridge configuration (all keys optional)."""

    mode: str | None = None            # "self-chat" | "bot"
    dm_policy: str | None = None       # "open" | "allowlist" | "disabled"
    group_policy: str | None = None    # "open" | "allowlist" | "disabled"
    require_mention: bool | None = None
    allow_from: list[str] | None = None


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


@router.get("/bridge")
async def bridge_config() -> dict:
    """Effective WhatsApp bridge config (mode + policy gates)."""
    from app.core.config import settings

    return await get_bridge_config(default_mode=settings.BOT_RELATIONSHIP_MODE.replace("_", "-")
                                   if settings.BOT_RELATIONSHIP_MODE in ("self_chat",) else "self-chat")


@router.put("/bridge")
async def update_bridge_config(payload: BridgeConfigPayload) -> dict:
    """Apply a bridge config change and restart Hermes so it takes effect.

    Mode changes require a container restart (the gateway reads WHATSAPP_MODE
    once when spawning the Node bridge); policy gates live in config.yaml and
    ride along on the same restart.
    """
    update = payload.model_dump(exclude_none=True)
    if not update:
        raise HTTPException(status_code=400, detail="No bridge config fields supplied")
    try:
        result = await set_bridge_config(update)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {**update, **result}
