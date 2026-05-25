"""Google OAuth callback endpoint."""
import secrets

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select

from app.core.config import settings
from app.core.logging import get_logger
from app.db.database import AsyncSessionLocal
from app.db.redis_client import cache_get, cache_set
from app.models.models import User
from app.services.oauth_service import (
    build_authorization_url,
    exchange_code_for_tokens,
    store_user_credentials,
)

router = APIRouter()
logger = get_logger(__name__)


@router.get("/oauth/start")
async def oauth_start(phone: str = Query(..., description="WhatsApp phone of the user to link")) -> dict:
    """Generate the Google authorization URL. Send the resulting URL to the user."""
    state = secrets.token_urlsafe(24)
    await cache_set(f"oauth_state:{state}", phone.lstrip("+"), ttl_seconds=600)
    return {"authorization_url": build_authorization_url(state)}


@router.get("/oauth/callback")
async def oauth_callback(code: str, state: str) -> dict:
    phone = await cache_get(f"oauth_state:{state}")
    if not phone:
        raise HTTPException(status_code=400, detail="Invalid or expired OAuth state")

    try:
        creds = exchange_code_for_tokens(code)
    except Exception as exc:
        logger.error("Token exchange failed: %s", exc)
        raise HTTPException(status_code=400, detail="Authorization code exchange failed") from exc

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(User).where(User.wa_phone == phone))
        user = result.scalar_one_or_none()
        if not user:
            user = User(wa_phone=phone, is_owner=(phone == settings.OWNER_WA_PHONE.lstrip("+")))
            db.add(user)
            await db.commit()
            await db.refresh(user)

        await store_user_credentials(db, user, creds)

    return {"status": "connected", "phone": phone}
