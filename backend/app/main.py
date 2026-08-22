"""FastAPI application entry point."""
import asyncio
from contextlib import asynccontextmanager

import os
from fastapi import FastAPI, Response
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from app.api import health, oauth, permissions, setup, webhooks, whatsapp_pairing
from app.core.config import settings
from app.core.logging import get_logger, setup_logging
from app.db.redis_client import close_redis, ensure_consumer_group, get_redis
from app.services.whatsapp_service import whatsapp_service, normalize_phone_number

setup_logging()
logger = get_logger(__name__)


async def _ensure_instance_created() -> None:
    """Make sure Evolution API has our WhatsApp instance. Retries during startup."""
    for attempt in range(12):
        try:
            info = await whatsapp_service.instance_status()
            # If we can read state, the instance exists already — good.
            if info and info.get("instance", {}).get("state") is not None:
                await whatsapp_service.configure_webhook()
                logger.info(
                    "WhatsApp instance exists (state=%s)",
                    info["instance"].get("state"),
                )
                return
            # No instance yet — create one.
            ok = await whatsapp_service.create_instance()
            if ok:
                logger.info("WhatsApp instance auto-created on startup")
                return
        except Exception as exc:
            logger.debug("Instance check attempt %d failed: %s", attempt + 1, exc)
        await asyncio.sleep(10)
    logger.warning(
        "Gave up auto-creating WhatsApp instance — call POST /setup/create-instance manually"
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting %s v%s", settings.APP_NAME, settings.APP_VERSION)
    await get_redis()
    await ensure_consumer_group()
    
    # Assert evolution_api database for Evolution API session persistence
    from sqlalchemy import text
    try:
        from sqlalchemy.ext.asyncio import create_async_engine
        base_url = settings.database_url.rsplit("/", 1)[0]
        admin_engine = create_async_engine(f"{base_url}/postgres", isolation_level="AUTOCOMMIT")
        async with admin_engine.connect() as conn:
            res = await conn.execute(text("SELECT 1 FROM pg_database WHERE datname='evolution_api'"))
            if not res.scalar():
                await conn.execute(text("CREATE DATABASE evolution_api"))
                logger.info("Lifespan startup: Created 'evolution_api' database in PostgreSQL for WhatsApp session persistence.")
        await admin_engine.dispose()
    except Exception as evo_db_err:
        logger.debug("Evolution DB assertion check: %s", evo_db_err)

    # Assert trust_level column in users table
    from app.db.database import AsyncSessionLocal
    
    async with AsyncSessionLocal() as db:
        try:
            await db.execute(text("ALTER TABLE users ADD COLUMN trust_level VARCHAR(16) DEFAULT 'trusted'"))
            await db.commit()
            logger.info("Database migration check: trust_level column added")
        except Exception as e:
            await db.rollback()
            logger.debug("Column trust_level assertion status (might already exist): %s", e)

        try:
            await db.execute(text("""
                CREATE TABLE IF NOT EXISTS api_keys (
                    id UUID PRIMARY KEY,
                    name VARCHAR(128) NOT NULL,
                    provider VARCHAR(64) NOT NULL,
                    api_key_enc TEXT NOT NULL,
                    is_active BOOLEAN DEFAULT TRUE,
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    updated_at TIMESTAMPTZ DEFAULT NOW()
                )
            """))
            await db.commit()
            logger.info("Database migration check: api_keys table created or verified")
            
            # Rebuild LiteLLM configuration on startup to propagate active DB keys
            try:
                from app.services.litellm_service import rebuild_litellm_config
                await rebuild_litellm_config(db)
                logger.info("LiteLLM configuration rebuilt successfully on startup")
            except Exception as config_err:
                logger.error("Failed to rebuild LiteLLM config on startup: %s", config_err)
        except Exception as e:
            await db.rollback()
            logger.error("Failed to assert api_keys table on startup: %s", e)


    # Assert owner synchronization with settings.OWNER_WA_PHONE or connected bot phone
    async with AsyncSessionLocal() as db:
        try:
            from app.models.models import User
            from sqlalchemy import select, update
            
            # Find current owner in DB first
            owner_res = await db.execute(
                select(User).where(User.is_owner == True)
            )
            current_owner = owner_res.scalar_one_or_none()

            connected_phone = await whatsapp_service.get_bot_phone()
            if connected_phone:
                owner_phone = normalize_phone_number(connected_phone)
                settings.OWNER_WA_PHONE = owner_phone
                logger.info("Lifespan startup: Dynamic owner phone resolved from active WhatsApp session: %s", owner_phone)
            else:
                if current_owner and current_owner.wa_phone:
                    owner_phone = current_owner.wa_phone
                    settings.OWNER_WA_PHONE = owner_phone
                    logger.info("Lifespan startup: Evolution API offline/slow, using current DB owner phone: %s", owner_phone)
                else:
                    owner_phone = normalize_phone_number(settings.OWNER_WA_PHONE) or settings.OWNER_WA_PHONE.lstrip("+")
                    settings.OWNER_WA_PHONE = owner_phone
                    logger.info("Lifespan startup: Fallback owner phone from settings: %s", owner_phone)

            if owner_phone:
                # Check duplicate user to prevent unique constraints error
                dup_res = await db.execute(
                    select(User).where(User.wa_phone == owner_phone)
                )
                dup_user = dup_res.scalar_one_or_none()
                
                if current_owner:
                    if current_owner.wa_phone != owner_phone:
                        if dup_user and dup_user.id != current_owner.id:
                            await db.delete(dup_user)
                            await db.flush()
                        current_owner.wa_phone = owner_phone
                        current_owner.has_permission = True
                        logger.info("Synchronized owner phone change to %s on startup", owner_phone)
                else:
                    if dup_user:
                        dup_user.is_owner = True
                        dup_user.has_permission = True
                        logger.info("Synchronized owner status for existing user %s", owner_phone)
                    else:
                        new_owner = User(
                            wa_phone=owner_phone,
                            is_owner=True,
                            has_permission=True,
                            display_name="You (Owner)"
                        )
                        db.add(new_owner)
                        logger.info("Created owner user %s on startup", owner_phone)
                
                # 2. Update all other users to is_owner = False
                await db.execute(
                    update(User).where(User.wa_phone != owner_phone).values(is_owner=False)
                )
                await db.commit()
                logger.info("Owner records synchronized successfully.")

                # If bot is in dual_number mode, configure agent webhook
                from app.services.preferences_service import preferences_service
                owner_id = current_owner.id if current_owner else None
                if not owner_id:
                    result = await db.execute(select(User).where(User.is_owner == True))
                    owner_user = result.scalar_one_or_none()
                    owner_id = owner_user.id if owner_user else None

                if owner_id:
                    bot_mode = await preferences_service.get(owner_id, "bot_mode")
                    if bot_mode == "dual_number":
                        from app.services.agent_instance_service import agent_instance_service
                        logger.info("Lifespan startup: Bot is in dual_number mode, ensuring agent instance status/webhook configuration")
                        try:
                            await agent_instance_service.configure_agent_webhook()
                        except Exception as agent_webhook_err:
                            logger.error("Failed to configure agent webhook on startup: %s", agent_webhook_err)

                # Sync Google OAuth credentials to Hermes on startup
                from app.services.oauth_service import load_user_credentials, sync_credentials_to_hermes
                result = await db.execute(select(User).where(User.is_owner == True))
                owner_user = result.scalar_one_or_none()
                if owner_user and owner_user.google_access_token_enc:
                    creds = await load_user_credentials(owner_user, db)
                    if creds:
                        sync_credentials_to_hermes(creds)
                        logger.info("Startup: Synced Google OAuth credentials to Hermes container.")
        except Exception as e:
            await db.rollback()
            logger.error("Failed to synchronize owner records on startup: %s", e)

    asyncio.create_task(_ensure_instance_created())
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
app.include_router(whatsapp_pairing.router, prefix="/api/pairing", tags=["pairing"])


@app.get("/")
async def root() -> dict:
    return {
        "app": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "docs": "/docs",
        "setup": "/setup/qr-status",
    }
