"""FastAPI application entry point."""
from contextlib import asynccontextmanager

import os
from fastapi import FastAPI, Response
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from app.api import contacts, health, oauth, permissions, setup, webhooks, whatsapp_pairing
from app.core.config import settings
from app.core.logging import get_logger, setup_logging
from app.db.redis_client import close_redis, get_redis
from app.intake.runtime import start_consumer, stop_consumer

setup_logging()
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting %s v%s", settings.APP_NAME, settings.APP_VERSION)
    await get_redis()
    await start_consumer()  # intake consumer: PENDING reclaim on boot (ADR-0007)

    # Schema is owned by Alembic (`alembic upgrade head`) — never DDL here (#9).
    from app.db.database import AsyncSessionLocal

    # Rebuild LiteLLM configuration to propagate active DB keys
    async with AsyncSessionLocal() as db:
        try:
            from app.services.litellm_service import rebuild_litellm_config
            await rebuild_litellm_config(db)
            logger.info("LiteLLM configuration rebuilt successfully on startup")
        except Exception as config_err:
            logger.error("Failed to rebuild LiteLLM config on startup: %s", config_err)

    # Non-destructive owner synchronisation (candidate 5 / #9)
    from app.services.bootstrap_service import ensure_owner_record

    async with AsyncSessionLocal() as db:
        try:
            owner_phone = await ensure_owner_record(db)
            logger.info("Bootstrap: owner records synchronised (%s)", owner_phone)

            # Sync Google OAuth credentials to Hermes on startup
            from app.models.models import User
            from sqlalchemy import select
            from app.services.oauth_service import load_user_credentials, sync_credentials_to_hermes

            result = await db.execute(select(User).where(User.is_owner == True))  # noqa: E712
            owner_user = result.scalar_one_or_none()
            if owner_user and owner_user.google_access_token_enc:
                creds = await load_user_credentials(owner_user, db)
                if creds:
                    sync_credentials_to_hermes(creds)
                    logger.info("Startup: Synced Google OAuth credentials to Hermes container.")
        except Exception as e:
            await db.rollback()
            logger.error("Failed to synchronise owner records on startup: %s", e)

    yield
    from app.services import bridge_client
    await stop_consumer()
    await bridge_client.close()
    await close_redis()
    logger.info("Shutdown complete")


app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    debug=settings.DEBUG,
    lifespan=lifespan,
)

static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.exists(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

@app.get("/logo.svg")
async def get_logo_svg():
    logo_path = os.path.join(static_dir, "logo.svg")
    if os.path.exists(logo_path):
        return FileResponse(logo_path, media_type="image/svg+xml")
    return Response(status_code=404)

@app.get("/logo.png")
async def get_logo_png():
    logo_path = os.path.join(static_dir, "logo.png")
    if os.path.exists(logo_path):
        return FileResponse(logo_path, media_type="image/png")
    return Response(status_code=404)

@app.get("/favicon.ico")
async def get_favicon():
    logo_path = os.path.join(static_dir, "logo.png")
    if os.path.exists(logo_path):
        return FileResponse(logo_path, media_type="image/png")
    svg_path = os.path.join(static_dir, "logo.svg")
    if os.path.exists(svg_path):
        return FileResponse(svg_path, media_type="image/svg+xml")
    return Response(status_code=404)

app.include_router(health.router, tags=["health"])
app.include_router(webhooks.router, tags=["webhooks"])
app.include_router(setup.router, tags=["setup"])
app.include_router(oauth.router, tags=["oauth"])
app.include_router(permissions.router, tags=["permissions"])
app.include_router(contacts.router, prefix="/api/contacts", tags=["contacts"])
app.include_router(whatsapp_pairing.router, prefix="/api/pairing", tags=["pairing"])


@app.get("/")
async def root() -> dict:
    return {
        "app": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "docs": "/docs",
        "setup": "/setup/qr-status",
    }
