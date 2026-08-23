"""Tests for password rotation (change-password endpoint + service)."""
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.main import app
from app.core.security import hash_password, verify_password
from app.core.auth import create_session, SESSION_COOKIE
from app.models.models import Tenant, DashboardUser
from app.db.database import AsyncSessionLocal


async def _seed(email: str, password: str) -> int:
    async with AsyncSessionLocal() as db:
        t = Tenant(name="Rot", slug=f"rot-{email}", is_active=True)
        db.add(t)
        await db.flush()
        d = DashboardUser(
            tenant_id=t.id, email=email,
            password_hash=hash_password(password), is_owner=True,
        )
        db.add(d)
        await db.commit()
        await db.refresh(d)
        return t.id, d.id


@pytest.mark.asyncio
async def test_change_password_updates_hash(test_engine):
    """Correct current password → hash rotated; new password verifies."""
    tenant_id, dash_id = await _seed("a@rot.test", "oldpass99")

    async with AsyncSessionLocal() as db:
        u = (await db.execute(select(DashboardUser).where(DashboardUser.id == dash_id))).scalar_one()

        from app.services.auth_service import update_password
        ok = await update_password(db, u, current="oldpass99", new_pass="newpass77")
        assert ok is True

    async with AsyncSessionLocal() as db:
        u = (await db.execute(select(DashboardUser).where(DashboardUser.id == dash_id))).scalar_one()
        assert verify_password("newpass77", u.password_hash)
        assert not verify_password("oldpass99", u.password_hash)


@pytest.mark.asyncio
async def test_change_password_rejects_wrong_current(test_engine):
    tenant_id, dash_id = await _seed("b@rot.test", "oldpass99")

    async with AsyncSessionLocal() as db:
        u = (await db.execute(select(DashboardUser).where(DashboardUser.id == dash_id))).scalar_one()
        from app.services.auth_service import update_password
        assert await update_password(db, u, current="WRONG", new_pass="newpass77") is False


@pytest.mark.asyncio
async def test_change_password_enforces_policy(test_engine):
    """New password must meet the same 8-char minimum as seeding."""
    from app.services.auth_service import update_password

    class FakeUser:
        password_hash = hash_password("whatever1")
        email = "fake@rot.test"

    db = object()  # policy check runs before any DB access
    with pytest.raises(ValueError):
        await update_password(db, FakeUser(), "whatever1", "short")


@pytest.mark.asyncio
async def test_change_password_route_end_to_end(test_engine):
    """POST /api/change-password with session → rotates; old stops working;
    new logs in."""
    tenant_id, dash_id = await _seed("c@rot.test", "firstpass1")
    sid = await create_session(tenant_id, dash_id)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        cookies={SESSION_COOKIE: sid},
    ) as ac:
        resp = await ac.post(
            "/api/change-password",
            json={"current_password": "firstpass1", "new_password": "secondpass2"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "changed"

    # old password no longer logs in; new one does
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        r_old = await ac.post("/login", data={"email": "c@rot.test", "password": "firstpass1"}, follow_redirects=False)
        r_new = await ac.post("/login", data={"email": "c@rot.test", "password": "secondpass2"}, follow_redirects=False)
        assert "error=true" in r_old.headers["location"]
        assert r_new.status_code == 303


@pytest.mark.asyncio
async def test_change_password_requires_session(test_engine):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(
            "/api/change-password",
            json={"current_password": "x", "new_password": "yyyyyyyy"},
        )
        assert resp.status_code == 401
