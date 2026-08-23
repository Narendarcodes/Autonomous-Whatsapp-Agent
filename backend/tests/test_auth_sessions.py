"""Tests for tenant-scoped session auth (Task A3).

Follows house pattern: httpx.AsyncClient + ASGITransport, test_engine creates
schema in the *_test Postgres DB; real Redis for sessions.
"""
import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.core.security import hash_password
from app.core.auth import create_session, destroy_session, get_principal, SESSION_COOKIE
from app.models.models import DashboardUser, Tenant
from app.db.database import AsyncSessionLocal


async def _seed(email: str, password: str) -> DashboardUser:
    """Seed a Tenant + DashboardUser. Relies on test_engine having created tables."""
    async with AsyncSessionLocal() as db:
        t = Tenant(name="Acme", slug=f"acme-{email}", is_active=True)
        db.add(t)
        await db.flush()
        u = DashboardUser(
            tenant_id=t.id,
            email=email,
            password_hash=hash_password(password),
            is_owner=True,
        )
        db.add(u)
        await db.commit()
        await db.refresh(u)
        return u


class FakeRequest:
    def __init__(self, cookies: dict | None = None):
        self.cookies = cookies or {}


@pytest.mark.asyncio
async def test_create_and_resolve_session(test_engine):
    u = await _seed("alice@acme.test", "pw123456")
    sid = await create_session(u.tenant_id, u.id)
    assert sid

    principal = await get_principal(FakeRequest({SESSION_COOKIE: sid}))
    assert principal.tenant_id == u.tenant_id
    assert principal.dashboard_user_id == u.id


@pytest.mark.asyncio
async def test_missing_cookie_401(test_engine):
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as ei:
        await get_principal(FakeRequest({}))
    assert ei.value.status_code == 401


@pytest.mark.asyncio
async def test_unknown_sid_401(test_engine):
    from fastapi import HTTPException

    req = FakeRequest({SESSION_COOKIE: "nope"})
    with pytest.raises(HTTPException) as ei:
        await get_principal(req)
    assert ei.value.status_code == 401


@pytest.mark.asyncio
async def test_destroy_revokes_instantly(test_engine):
    u = await _seed("bob@acme.test", "pw123456")
    sid = await create_session(u.tenant_id, u.id)
    req = FakeRequest({SESSION_COOKIE: sid})
    p = await get_principal(req)
    assert p.tenant_id == u.tenant_id

    await destroy_session(req)  # instant revoke — the reason we chose Redis over JWT
    from fastapi import HTTPException

    with pytest.raises(HTTPException):
        await get_principal(req)


@pytest.mark.asyncio
async def test_login_route_sets_tenant_cookie(test_engine):
    """Full route: POST /login with dashboard creds → 303 + omniwa_session cookie → resolves tenant."""
    u = await _seed("carol@acme.test", "supersafe9")

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        resp = await ac.post(
            "/login",
            data={"email": "carol@acme.test", "password": "supersafe9"},
            follow_redirects=False,
        )
        assert resp.status_code == 303, resp.text
        set_cookies = resp.headers.get_list("set-cookie")
        matching = [c for c in set_cookies if c.startswith(SESSION_COOKIE)]
        assert matching, f"omniwa_session cookie not set: {set_cookies}"

        # Cookie value must resolve to the right tenant
        sid = matching[0].split(";")[0].split("=", 1)[1]
        principal = await get_principal(FakeRequest({SESSION_COOKIE: sid}))
        assert principal.tenant_id == u.tenant_id
        assert principal.dashboard_user_id == u.id


@pytest.mark.asyncio
async def test_login_wrong_password_rejected(test_engine):
    await _seed("dave@acme.test", "rightpass1")

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        resp = await ac.post(
            "/login",
            data={"email": "dave@acme.test", "password": "WRONG"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "error=true" in resp.headers["location"]


@pytest.mark.asyncio
async def test_logout_kills_session(test_engine):
    u = await _seed("erin@acme.test", "pw123456")
    sid = await create_session(u.tenant_id, u.id)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        cookies={SESSION_COOKIE: sid},
    ) as ac:
        resp = await ac.get("/logout", follow_redirects=False)
        assert resp.status_code == 302

        from fastapi import HTTPException

        # Session must be dead server-side (not just cookie deletion)
        with pytest.raises(HTTPException):
            await get_principal(FakeRequest({SESSION_COOKIE: sid}))
