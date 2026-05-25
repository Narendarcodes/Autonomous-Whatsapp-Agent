"""FastAPI application entry point."""
import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import health, oauth, setup, webhooks
from app.core.config import settings
from app.core.logging import get_logger, setup_logging
from app.db.redis_client import close_redis, ensure_consumer_group, get_redis
from app.services.whatsapp_service import whatsapp_service

setup_logging()
logger = get_logger(__name__)


async def _try_register_webhook_with_retries() -> None:
    """Best-effort: tell OpenWA to deliver webhooks to us. Retries during startup."""
    for attempt in range(12):
        try:
            info = await whatsapp_service.session_status()
        except Exception:
            info = None
        if info and info.get("status") in ("READY", "AUTHENTICATED"):
            ok = await whatsapp_service.register_webhook()
            if ok:
                return
        await asyncio.sleep(10)
    logger.warning("Gave up trying to auto-register webhook with OpenWA")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting %s v%s", settings.APP_NAME, settings.APP_VERSION)
    await get_redis()
    await ensure_consumer_group()
    asyncio.create_task(_try_register_webhook_with_retries())
    yield
    await whatsapp_service.close()
    await close_redis()
    logger.info("Shutdown complete")


app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    debug=settings.DEBUG,
    lifespan=lifespan,
)

app.include_router(health.router, tags=["health"])
app.include_router(webhooks.router, tags=["webhooks"])
app.include_router(setup.router, tags=["setup"])
app.include_router(oauth.router, tags=["oauth"])


@app.get("/")
async def root() -> dict:
    return {
        "app": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "docs": "/docs",
        "setup": "/setup/qr-status",
    }
