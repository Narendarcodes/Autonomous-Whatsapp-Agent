"""Health check endpoints."""
from fastapi import APIRouter
from sqlalchemy import text

from app.db.database import AsyncSessionLocal
from app.db.redis_client import get_redis
from app.services.whatsapp_service import whatsapp_service

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
        session = await whatsapp_service.session_status()
        status["openwa"] = session.get("status") if session else "unreachable"
    except Exception as exc:
        status["openwa"] = f"error: {exc}"

    return status
