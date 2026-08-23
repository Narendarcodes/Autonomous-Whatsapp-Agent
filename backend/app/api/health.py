"""Health check endpoints."""
import httpx
from fastapi import APIRouter
from sqlalchemy import text

from app.core.config import settings
from app.db.database import AsyncSessionLocal
from app.db.redis_client import get_redis

router = APIRouter()


@router.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@router.get("/health/detailed")
async def health_detailed() -> dict:
    status: dict = {"app": "ok"}

    try:
        async with AsyncSessionLocal() as db:
            await db.execute(text("SELECT 1"))
        status["postgres"] = "ok"
    except Exception as exc:
        status["postgres"] = f"error: {exc}"

    try:
        r = await get_redis()
        pong = await r.ping()
        status["redis"] = "ok" if pong else "no_pong"
    except Exception as exc:
        status["redis"] = f"error: {exc}"

    try:
        from app.services.bridge_client import bridge_status
        status["whatsapp_bridge"] = await bridge_status()
    except Exception as exc:
        status["whatsapp_bridge"] = f"error: {exc}"

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(
                f"{settings.HERMES_BASE_URL.rstrip('/')}/health",
                headers={"Authorization": f"Bearer {settings.HERMES_API_KEY}"},
            )
        status["hermes"] = "ok" if resp.status_code == 200 else f"http_{resp.status_code}"
    except Exception as exc:
        status["hermes"] = f"error: {exc}"

    return status
