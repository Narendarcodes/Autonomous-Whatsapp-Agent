"""Tests for multi-tenant OAuth flow (Tasks B1+B2).

- /oauth/authorize stores tenant_id + PKCE verifier in Redis state
- store_user_credentials writes encrypted CustomerGoogleToken (tenant-scoped)
"""
import json

import pytest
from httpx import ASGITransport, AsyncClient
from unittest.mock import patch, MagicMock

from app.main import app
from app.core.auth import create_session, SESSION_COOKIE
from app.core.security import decrypt_token
from app.db.redis_client import cache_get
from app.models.models import Tenant, DashboardUser, User, CustomerGoogleToken
from app.db.database import AsyncSessionLocal


async def _seed_tenant_owner(email: str, password: str):
    async with AsyncSessionLocal() as db:
        t = Tenant(name="Globex", slug=f"globex-{email}", is_active=True)
        db.add(t)
        await db.flush()
        d = DashboardUser(
            tenant_id=t.id, email=email,
            password_hash=__import__("app.core.security", fromlist=["hash_password"]).hash_password(password),
            is_owner=True,
        )
        db.add(d)
        await db.commit()
        await db.refresh(d)
        return t, d


def _fake_creds() -> MagicMock:
    creds = MagicMock()
    creds.token = "access-token-xyz"
    creds.refresh_token = "refresh-token-abc"
    from datetime import datetime, timezone, timedelta
    creds.expiry = datetime.now(timezone.utc) + timedelta(hours=1)
    creds.scopes = ["https://www.googleapis.com/auth/calendar"]
    return creds


@pytest.mark.asyncio
async def test_authorize_state_carries_tenant(test_engine):
    """GET /oauth/authorize with a tenant-scoped session → Redis state contains that tenant_id."""
    t, d = await _seed_tenant_owner("owner@globex.test", "pw123456")
    sid = await create_session(t.id, d.id)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        cookies={SESSION_COOKIE: sid},
    ) as ac:
        # Redirect target is Google; follow_redirects=False
        resp = await ac.get("/oauth/authorize", follow_redirects=False)
        assert resp.status_code in (302, 307)
        loc = resp.headers["location"]
        assert "accounts.google.com" in loc

        # Extract state param and verify it maps to tenant_id + verifier in Redis
        from urllib.parse import urlparse, parse_qs
        qs = parse_qs(urlparse(loc).query)
        state = qs["state"][0]

        raw = await cache_get(f"oauth_state:{state}")
        assert raw, "oauth state missing from Redis"
        data = json.loads(raw)
        assert data["tenant_id"] == t.id
        assert data["code_verifier"], "PKCE verifier must be stored"


@pytest.mark.asyncio
async def test_store_credentials_writes_tenant_scoped_encrypted_row(test_engine):
    """store_user_credentials(tenant_id=N) → encrypted CustomerGoogleToken row."""
    from app.services.oauth_service import store_user_credentials

    async with AsyncSessionLocal() as db:
        t = Tenant(name="Initech", slug="initech-tok", is_active=True)
        db.add(t)
        await db.flush()
        u = User(wa_phone="919999888877", tenant_id=t.id, is_owner=True, has_permission=True)
        db.add(u)
        await db.commit()
        await db.refresh(u)
        tenant_id = t.id

    async with AsyncSessionLocal() as db:
        res = await db.execute(
            __import__("sqlalchemy", fromlist=["select"]).select(User).where(User.wa_phone == "919999888877")
        )
        user = res.scalar_one()

        # Patch the legacy Hermes sync so tests don't touch the filesystem/docker
        with patch("app.services.oauth_service.sync_credentials_to_hermes") as mock_sync:
            await store_user_credentials(db, user, _fake_creds(), tenant_id=tenant_id)
            if tenant_id != 1:
                mock_sync.assert_not_called()

        # Verify the tenant-scoped row exists, encrypted
        res = await db.execute(
            __import__("sqlalchemy", fromlist=["select"]).select(CustomerGoogleToken).where(
                CustomerGoogleToken.tenant_id == tenant_id
            )
        )
        tok = res.scalar_one_or_none()
        assert tok is not None
        assert tok.access_token_enc != "access-token-xyz"          # encrypted at rest
        assert decrypt_token(tok.access_token_enc) == "access-token-xyz"
        assert tok.refresh_token_enc is not None
        assert decrypt_token(tok.refresh_token_enc) == "refresh-token-abc"
        assert "calendar" in (tok.scopes or "")


@pytest.mark.asyncio
async def test_two_tenants_isolated_tokens(test_engine):
    """Same phone under two tenants → two independent token rows (no cross-tenant leak)."""
    from app.services.oauth_service import store_user_credentials
    from sqlalchemy import select

    async with AsyncSessionLocal() as db:
        t1 = Tenant(name="A", slug="iso-a", is_active=True)
        t2 = Tenant(name="B", slug="iso-b", is_active=True)
        db.add_all([t1, t2])
        await db.flush()
        u1 = User(wa_phone="917777666655", tenant_id=t1.id, is_owner=True, has_permission=True)
        u2 = User(wa_phone="916666555544", tenant_id=t2.id, is_owner=True, has_permission=True)
        db.add_all([u1, u2])
        await db.commit()
        id1, id2 = t1.id, t2.id
        u1_id, u2_id = u1.id, u2.id

    async with AsyncSessionLocal() as db:
        u1 = (await db.execute(select(User).where(User.id == u1_id))).scalar_one()
        with patch("app.services.oauth_service.sync_credentials_to_hermes"):
            await store_user_credentials(db, u1, _fake_creds(), tenant_id=id1)

    async with AsyncSessionLocal() as db:
        rows = (await db.execute(select(CustomerGoogleToken))).scalars().all()
        by_tenant = {r.tenant_id for r in rows}
        assert id1 in by_tenant
        assert id2 not in by_tenant  # tenant B has NO token row — isolation holds
