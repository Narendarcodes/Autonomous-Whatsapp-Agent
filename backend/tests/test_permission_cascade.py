"""Tests for the message-level permission cascade (Task C1).

Owner → run instantly. Authorized → run. Stranger → hold + owner notified.
Only the bridge send function is mocked (owner DM); DB is the real test Postgres.
"""
import pytest
from unittest.mock import patch, AsyncMock
from sqlalchemy import select

from app.models.models import User, Tenant, PendingDecision
from app.db.database import AsyncSessionLocal
from app.services.permission_service import permission_service


async def _mk_user(phone: str, *, owner=False, permitted=False, tenant_id=None) -> str:
    async with AsyncSessionLocal() as db:
        u = User(wa_phone=phone, is_owner=owner, has_permission=permitted, tenant_id=tenant_id)
        db.add(u)
        await db.commit()
    return phone


@pytest.mark.asyncio
async def test_owner_runs_instantly(test_engine):
    await _mk_user("911110000000", owner=True)
    async with AsyncSessionLocal() as db:
        result = await permission_service.decide(db, "+911110000000", "book a table")
        assert result["action"] == "run"
        assert result["needs_owner_approval"] is False


@pytest.mark.asyncio
async def test_authorized_user_runs(test_engine):
    await _mk_user("922200000000", permitted=True)
    async with AsyncSessionLocal() as db:
        result = await permission_service.decide(db, "+922200000000", "summarize my day")
        assert result["action"] == "run"


@pytest.mark.asyncio
async def test_stranger_held_and_owner_notified(test_engine):
    with patch(
        "app.services.permission_service.bridge_send_text",
        new=AsyncMock(),
    ) as mock_send:
        async with AsyncSessionLocal() as db:
            result = await permission_service.decide(db, "933330000000", "email my boss")

        assert result["action"] == "hold"
        assert result["needs_owner_approval"] is True
        assert result["decision"] is not None
        assert result["decision"].status == "awaiting"
        assert result["decision"].short_code

        # Owner got the approval prompt via WhatsApp
        mock_send.assert_awaited_once()
        args = mock_send.await_args
        assert "Approval needed" in args.args[1] or "Approval needed" in args.kwargs.get("message", "")


@pytest.mark.asyncio
async def test_unknown_sender_gets_row_with_tenant(test_engine):
    """First-time sender: user row created scoped to the caller's tenant."""
    async with AsyncSessionLocal() as db:
        t = Tenant(name="T", slug="casc-t", is_active=True)
        db.add(t)
        await db.commit()
        tid = t.id

    with patch("app.services.permission_service.bridge_send_text", new=AsyncMock()):
        async with AsyncSessionLocal() as db:
            result = await permission_service.decide(db, "944440000000", "hi", tenant_id=tid)

    async with AsyncSessionLocal() as db:
        u = (
            await db.execute(select(User).where(User.wa_phone == "944440000000"))
        ).scalar_one()
        assert u.tenant_id == tid
        assert u.has_permission is False


@pytest.mark.asyncio
async def test_hold_then_owner_approves_via_code(test_engine):
    """Full cascade: stranger held → owner replies '<CODE> yes' → decision approved."""
    with patch("app.services.permission_service.bridge_send_text", new=AsyncMock()):
        async with AsyncSessionLocal() as db:
            result = await permission_service.decide(db, "955550000000", "create a doc")
            code = result["decision"].short_code

        async with AsyncSessionLocal() as db:
            resolved = await permission_service.try_resolve(db, f"{code} yes")

    assert resolved is not None
    assert resolved.status == "approved"
