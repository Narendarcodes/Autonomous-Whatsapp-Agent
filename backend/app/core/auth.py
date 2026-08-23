"""Tenant-scoped session auth (Redis-backed, instant revocation).

Replaces the single ADMIN_PASSWORD + naru_session cookie.
Session value in Redis: "tenant_id:dashboard_user_id" (TTL 24h).
JWT is NOT used — Redis gives instant logout/compromise kill.
"""
import secrets
from dataclasses import dataclass

from fastapi import Request, HTTPException

from app.db.redis_client import cache_get, cache_set

SESSION_COOKIE = "omniwa_session"
SESSION_TTL_SECONDS = 86400  # 24h


@dataclass
class DashboardPrincipal:
    """The authenticated dashboard identity for one request."""

    tenant_id: int
    dashboard_user_id: int
    is_owner: bool = False
    email: str = ""


def _session_key(sid: str) -> str:
    return f"dash_session:{sid}"


async def create_session(tenant_id: int, dashboard_user_id: int) -> str:
    """Create a server-side session; returns the cookie value (opaque sid)."""
    sid = secrets.token_urlsafe(32)
    await cache_set(_session_key(sid), f"{tenant_id}:{dashboard_user_id}", ttl_seconds=SESSION_TTL_SECONDS)
    return sid


async def destroy_session(request: Request) -> None:
    """Instant revoke — delete the Redis key."""
    sid = request.cookies.get(SESSION_COOKIE)
    if sid:
        await cache_set(_session_key(sid), "", ttl_seconds=1)


async def get_principal(request: Request) -> DashboardPrincipal:
    """Resolve the current dashboard principal from the session cookie.

    Raises 401 if missing/expired. Every downstream query MUST filter by
    principal.tenant_id — this is the multi-tenant isolation boundary.
    """
    sid = request.cookies.get(SESSION_COOKIE)
    if not sid:
        raise HTTPException(status_code=401, detail="Unauthorized")

    val = await cache_get(_session_key(sid))
    if not val or ":" not in val:
        raise HTTPException(status_code=401, detail="Session expired")

    try:
        tenant_str, user_str = val.split(":", 1)
        tenant_id, user_id = int(tenant_str), int(user_str)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail="Malformed session") from exc

    # is_owner/email resolved by caller via DB when needed; keep the hot path Redis-only
    return DashboardPrincipal(tenant_id=tenant_id, dashboard_user_id=user_id)


async def require_auth(request: Request) -> DashboardPrincipal:
    """FastAPI dependency: 401 unless a valid session exists."""
    try:
        return await get_principal(request)
    except HTTPException:
        raise


def set_session_cookie(response, sid: str) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        sid,
        httponly=True,
        secure=True,       # behind Cloudflare tunnel (https) in prod; tests use TestClient with base_url https
        samesite="lax",
        max_age=SESSION_TTL_SECONDS,
    )


def clear_session_cookie(response) -> None:
    response.delete_cookie(SESSION_COOKIE)
