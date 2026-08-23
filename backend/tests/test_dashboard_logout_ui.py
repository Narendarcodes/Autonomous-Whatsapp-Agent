"""Dashboard must expose a logout control linking to GET /logout.

Regression: backend /logout existed and was fully tested, but no template
ever linked to it — users had no way to end their session from the UI.
"""
import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.core.security import hash_password
from app.core.auth import create_session, SESSION_COOKIE
from app.models.models import DashboardUser, Tenant
from app.db.database import AsyncSessionLocal


async def _seed(email: str, password: str) -> DashboardUser:
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


@pytest.mark.asyncio
async def test_dashboard_contains_logout_control(test_engine):
    u = await _seed("frank@acme.test", "pw123456")
    sid = await create_session(u.tenant_id, u.id)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        cookies={SESSION_COOKIE: sid},
    ) as ac:
        resp = await ac.get("/dashboard")
        assert resp.status_code == 200
        assert 'href="/logout"' in resp.text, (
            "Dashboard HTML must contain a logout link pointing at GET /logout"
        )
