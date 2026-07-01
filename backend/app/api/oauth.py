"""Google OAuth callback endpoint."""
import secrets
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from app.api.setup import verify_api_admin

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


def error_html(title: str, message: str) -> HTMLResponse:
    """Helper to return a premium, user-friendly HTML error page."""
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title} - Naru AI</title>
    <link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@500;700&family=Geist:wght@300;400;600&display=swap" rel="stylesheet"/>
    <style>
        body {{
            background: radial-gradient(circle at center, #1a0b0d 0%, #080304 100%);
            color: #ffeef0;
            font-family: 'Geist', sans-serif;
            display: flex;
            align-items: center;
            justify-content: center;
            height: 100vh;
            margin: 0;
            overflow: hidden;
        }}
        .card {{
            background: rgba(255, 255, 255, 0.03);
            backdrop-filter: blur(16px);
            -webkit-backdrop-filter: blur(16px);
            border: 1px solid rgba(255, 255, 255, 0.08);
            border-radius: 24px;
            padding: 48px;
            width: 100%;
            max-width: 420px;
            text-align: center;
            box-shadow: 0 20px 40px rgba(0, 0, 0, 0.5);
            animation: fadeIn 0.6s ease-out forwards;
        }}
        h1 {{
            font-family: 'Space Grotesk', sans-serif;
            font-size: 28px;
            margin-bottom: 16px;
            color: #e21e26;
            letter-spacing: -0.02em;
        }}
        p {{
            font-size: 15px;
            line-height: 1.6;
            color: #c9b1b4;
            margin-bottom: 32px;
        }}
        .btn {{
            display: inline-block;
            background: linear-gradient(135deg, #e21e26 0%, #b90015 100%);
            color: white;
            text-decoration: none;
            padding: 14px 28px;
            font-weight: 600;
            border-radius: 12px;
            transition: all 0.3s ease;
            box-shadow: 0 4px 12px rgba(226, 30, 38, 0.3);
        }}
        .btn:hover {{
            transform: translateY(-2px);
            box-shadow: 0 6px 20px rgba(226, 30, 38, 0.5);
        }}
        .icon {{
            font-size: 48px;
            margin-bottom: 24px;
            color: #e21e26;
        }}
        @keyframes fadeIn {{
            from {{ opacity: 0; transform: translateY(20px); }}
            to {{ opacity: 1; transform: translateY(0); }}
        }}
    </style>
</head>
<body>
    <div class="card">
        <div class="icon">⚠️</div>
        <h1>{title}</h1>
        <p>{message}</p>
        <a href="/dashboard" class="btn">Return to Dashboard</a>
    </div>
</body>
</html>"""
    return HTMLResponse(content=html, status_code=400)


@router.get("/oauth/authorize")
async def oauth_authorize(state: str = Query(None, description="Client-side state token"), dependencies=Depends(verify_api_admin)) -> RedirectResponse:
    """Simple one-click OAuth redirect. Stores state and redirects user to Google login."""
    # Generate a new state token if client didn't provide one
    oauth_state = state or secrets.token_urlsafe(24)
    
    # Store state, owner's phone, and code verifier for later callback
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(User).where(User.is_owner == True))
        owner = result.scalar_one_or_none()
        owner_phone = owner.wa_phone if owner else settings.OWNER_WA_PHONE.lstrip("+")
        
    auth_url, code_verifier = build_authorization_url(oauth_state)
    
    import json
    state_data = {
        "phone": owner_phone,
        "code_verifier": code_verifier
    }
    await cache_set(f"oauth_state:{oauth_state}", json.dumps(state_data), ttl_seconds=600)
    return RedirectResponse(url=auth_url)


@router.get("/oauth/start")
async def oauth_start(phone: str = Query(..., description="WhatsApp phone of the user to link"), dependencies=Depends(verify_api_admin)) -> dict:
    """Generate the Google authorization URL. Send the resulting URL to the user."""
    state = secrets.token_urlsafe(24)
    auth_url, code_verifier = build_authorization_url(state)
    
    import json
    state_data = {
        "phone": phone.lstrip("+"),
        "code_verifier": code_verifier
    }
    await cache_set(f"oauth_state:{state}", json.dumps(state_data), ttl_seconds=600)
    return {"authorization_url": auth_url}


@router.get("/oauth/callback")
async def oauth_callback(code: str, state: str) -> Any:
    """Callback from Google OAuth. Exchanges code for tokens and stores them."""
    state_value = await cache_get(f"oauth_state:{state}")
    if not state_value:
        return error_html(
            "Connection Expired", 
            "Your Google Authorization session has expired or is invalid. For security reasons, setup requests must be completed within 10 minutes."
        )

    # Parse JSON state or fallback to legacy plain phone string
    import json
    phone = state_value
    code_verifier = None
    try:
        data = json.loads(state_value)
        if isinstance(data, dict):
            phone = data.get("phone")
            code_verifier = data.get("code_verifier")
    except json.JSONDecodeError:
        pass

    try:
        creds = exchange_code_for_tokens(code, code_verifier=code_verifier)
    except Exception as exc:
        logger.error("Token exchange failed: %s", exc)
        return error_html(
            "Authorization Failed", 
            "Failed to exchange Google OAuth code for access tokens. Please try again."
        )

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(User).where(User.wa_phone == phone))
        user = result.scalar_one_or_none()
        if not user:
            # Fallback to active owner record to prevent token loss due to phone mismatches
            owner_res = await db.execute(select(User).where(User.is_owner == True))
            user = owner_res.scalar_one_or_none()
            if not user:
                user = User(wa_phone=phone, is_owner=(phone == settings.OWNER_WA_PHONE.lstrip("+")))
                db.add(user)
                await db.commit()
                await db.refresh(user)

        await store_user_credentials(db, user, creds)
        
        # Hot reload Hermes container to pick up Google tokens
        from app.services.docker_manager import docker_manager
        import asyncio
        asyncio.create_task(docker_manager.restart_hermes_agent())

    # Redirect to the dashboard with success parameter
    return RedirectResponse(url="/dashboard?google_success=true")
