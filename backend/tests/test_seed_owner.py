"""Tests for the dashboard owner seeding CLI (scripts/seed_owner.py)."""
import pytest
from sqlalchemy import select

from app.core.security import verify_password
from app.models.models import Tenant, DashboardUser
from app.db.database import AsyncSessionLocal


@pytest.mark.asyncio
async def test_seed_creates_tenant_and_owner(test_engine):
    """Fresh DB: seed creates default tenant + owner with argon2 hash."""
    from scripts.seed_owner import seed_owner

    created = await seed_owner(
        email="owner@mybiz.test",
        password="strong-pass-9",
        tenant_slug="mybiz",
        tenant_name="My Biz",
    )
    assert created is True

    async with AsyncSessionLocal() as db:
        t = (await db.execute(select(Tenant).where(Tenant.slug == "mybiz"))).scalar_one()
        u = (await db.execute(select(DashboardUser).where(DashboardUser.email == "owner@mybiz.test"))).scalar_one()
        assert u.tenant_id == t.id
        assert u.is_owner is True
        assert u.password_hash.startswith("$argon2")
        assert verify_password("strong-pass-9", u.password_hash)


@pytest.mark.asyncio
async def test_seed_is_idempotent(test_engine):
    """Re-running with the same email must not duplicate or crash."""
    from scripts.seed_owner import seed_owner

    first = await seed_owner("idem@x.test", "pw123456", "idem-tenant", "Idem")
    again = await seed_owner("idem@x.test", "other-pw", "idem-tenant", "Idem")

    assert first is True
    assert again is False  # already exists — no-op reported honestly

    async with AsyncSessionLocal() as db:
        rows = (await db.execute(
            select(DashboardUser).where(DashboardUser.email == "idem@x.test")
        )).scalars().all()
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_seed_rejects_weak_password(test_engine):
    """Password policy enforced at the CLI boundary."""
    from scripts.seed_owner import seed_owner

    with pytest.raises(ValueError):
        await seed_owner("weak@x.test", "short", "weak-t", "Weak")


@pytest.mark.asyncio
async def test_seeded_owner_can_log_in(test_engine):
    """End-to-end: seeded creds work against the real /login route."""
    from httpx import ASGITransport, AsyncClient
    from app.main import app
    from scripts.seed_owner import seed_owner
    from app.core.auth import SESSION_COOKIE

    await seed_owner("login@x.test", "goodpass99", "logint", "Login Test")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(
            "/login",
            data={"email": "login@x.test", "password": "goodpass99"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert any(SESSION_COOKIE in c for c in resp.headers.get_list("set-cookie"))
