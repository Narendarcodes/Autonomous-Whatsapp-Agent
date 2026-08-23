"""Google OAuth callback endpoint."""
import secrets
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Depends, Request
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
    <title>{title} - OmniWA</title>
    <link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@500;700&family=Geist:wght@300;400;600&display=swap" rel="stylesheet"/>
    <style>
        body {{
            background:
                radial-gradient(circle at 20% 20%, rgba(37, 211, 102, 0.18), transparent 24%),
                linear-gradient(180deg, #0A0D14 0%, #121418 100%);
            color: #f0fdf4;
            font-family: 'Geist', sans-serif;
            display: flex;
            align-items: center;
            justify-content: center;
            min-height: 100vh;
            margin: 0;
            overflow: hidden;
        }}
        .card {{
            background: rgba(255, 255, 255, 0.05);
            backdrop-filter: blur(18px);
            -webkit-backdrop-filter: blur(18px);
            border: 1px solid rgba(255, 255, 255, 0.1);
            border-radius: 28px;
            padding: 48px;
            width: 100%;
            max-width: 420px;
            text-align: center;
            box-shadow: 0 24px 80px rgba(0, 0, 0, 0.4);
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
            color: rgba(240, 253, 244, 0.72);
            margin-bottom: 32px;
        }}
        .btn {{
            display: inline-block;
            background: #25D366;
            color: #0A0D14;
            text-decoration: none;
            padding: 14px 28px;
            font-weight: 600;
            border-radius: 9999px;
            transition: all 0.3s ease;
            box-shadow: 0 12px 30px rgba(37, 211, 102, 0.22);
        }}
        .btn:hover {{
            transform: translateY(-2px);
            box-shadow: 0 16px 34px rgba(37, 211, 102, 0.28);
        }}
        .icon {{
            font-size: 48px;
            margin-bottom: 24px;
            color: #25D366;
        }}
        @keyframes fadeIn {{
            from {{ opacity: 0; transform: translateY(20px); }}
            to {{ opacity: 1; transform: translateY(0); }}
        }}
    </style>
</head>
<body>
    <div class="card">
        <div class="icon">⚠</div>
        <h1>{title}</h1>
        <p>{message}</p>
        <a href="/dashboard" class="btn">Return to Dashboard</a>
    </div>
</body>
</html>"""
    return HTMLResponse(content=html, status_code=400)


@router.get("/oauth/authorize")
async def oauth_authorize(
    request: Request,
    state: str = Query(None, description="Client-side state token"),
    dependencies=Depends(verify_api_admin),
) -> RedirectResponse:
    """One-click OAuth redirect from the dashboard. State carries tenant_id + PKCE verifier."""
    import json

    from app.core.auth import get_principal
    from fastapi import HTTPException

    # Resolve the authenticated dashboard principal → tenant scope for the token
    try:
        principal = await get_principal(request)
        tenant_id = principal.tenant_id
    except HTTPException:
        # Legacy session fallback → default tenant
        tenant_id = 1

    oauth_state = state or secrets.token_urlsafe(24)

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(User).where(User.is_owner == True))
        owner = result.scalar_one_or_none()
        owner_phone = owner.wa_phone if owner else settings.OWNER_WA_PHONE.lstrip("+")

    auth_url, code_verifier = build_authorization_url(oauth_state)

    state_data = {
        "phone": owner_phone,
        "code_verifier": code_verifier,
        "tenant_id": tenant_id,  # multi-tenant: callback writes the token HERE
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


@router.get("/connect-google")
async def connect_google() -> RedirectResponse:
    """Public alias advertised by the agent soul (SOUL.md tells users to visit
    https://api.narendar.tech/connect-google). Resolves the owner phone and
    redirects straight to Google's consent screen.

    Single-owner semantics: token lands on OWNER_WA_PHONE / default tenant,
    mirroring the legacy fallback in /oauth/authorize.
    """
    import json

    state = secrets.token_urlsafe(24)
    auth_url, code_verifier = build_authorization_url(state)
    state_data = {
        "phone": settings.OWNER_WA_PHONE.lstrip("+"),
        "code_verifier": code_verifier,
        "tenant_id": 1,
    }
    await cache_set(f"oauth_state:{state}", json.dumps(state_data), ttl_seconds=600)
    return RedirectResponse(url=auth_url)


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
    tenant_id = None
    try:
        data = json.loads(state_value)
        if isinstance(data, dict):
            phone = data.get("phone")
            code_verifier = data.get("code_verifier")
            tenant_id = data.get("tenant_id")  # multi-tenant scope (None = legacy)
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

        await store_user_credentials(db, user, creds, tenant_id=tenant_id)

        # Legacy dev flow only: hot reload Hermes to pick up default-tenant token
        if tenant_id is None or tenant_id == 1:
            try:
                from app.services.docker_manager import docker_manager
                import asyncio
                asyncio.create_task(docker_manager.restart_hermes_agent())
            except Exception:
                pass  # docker socket unavailable (dev) — sync_credentials_to_hermes already no-ops

    # Redirect to the dashboard with success parameter
    return RedirectResponse(url="/dashboard?google_success=true")
