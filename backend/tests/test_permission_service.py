import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock
from app.models.models import User, PendingDecision
from app.services.permission_service import permission_service
from app.services.whatsapp_service import whatsapp_service
from app.core.config import settings

@pytest.mark.asyncio
async def test_permission_is_required():
    """Verify check mappings for is_required matches settings parameters."""
    old_create = settings.PERMISSION_REQUIRED_FOR_CREATE
    old_delete = settings.PERMISSION_REQUIRED_FOR_DELETE
    try:
        settings.PERMISSION_REQUIRED_FOR_CREATE = True
        settings.PERMISSION_REQUIRED_FOR_DELETE = False
        
        assert await permission_service.is_required("create_event") is True
        assert await permission_service.is_required("delete_event") is False
    finally:
        settings.PERMISSION_REQUIRED_FOR_CREATE = old_create
        settings.PERMISSION_REQUIRED_FOR_DELETE = old_delete


@pytest.mark.asyncio
async def test_request_permission(db_session, mocker):
    """Test generating a PendingDecision and delivering confirmation notifications to the owner."""
    user = User(wa_phone="1234567890")
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)

    # Mock WhatsApp delivery
    mock_send = mocker.patch.object(whatsapp_service, "send_text", new_callable=AsyncMock)

    proposed = {"summary": "Doctor appointment", "start_time": "2026-06-15T15:00:00", "end_time": "2026-06-15T16:00:00"}
    
    decision = await permission_service.request_permission(
        db_session, user, "create_event", proposed, source_chat="919999999999@s.whatsapp.net"
    )

    assert decision.id is not None
    assert decision.user_id == user.id
    assert decision.action_type == "create_event"
    assert decision.proposed_action == proposed
    assert decision.status == "awaiting"
    assert len(decision.short_code) == 4
    assert decision.short_code.isalnum()
    
    # Check if a WhatsApp notification was sent to settings.OWNER_WA_PHONE
    mock_send.assert_called_once()
    called_args = mock_send.call_args[0]
    assert called_args[0] == settings.OWNER_WA_PHONE.lstrip("+")
    assert decision.short_code in called_args[1]
    assert "Doctor appointment" in called_args[1]


@pytest.mark.asyncio
async def test_try_resolve_decision(db_session):
    """Verify code resolution matching approved, rejected, or invalid verdicts."""
    user = User(wa_phone="1234567890")
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)

    # Seed an awaiting decision
    expires = datetime.now(timezone.utc) + timedelta(minutes=10)
    decision = PendingDecision(
        user_id=user.id,
        short_code="ABCD",
        action_type="create_event",
        proposed_action={},
        status="awaiting",
        expires_at=expires,
    )
    db_session.add(decision)
    await db_session.commit()

    # 1. Resolve to Approved (e.g. ABCD yes)
    res = await permission_service.try_resolve(db_session, "ABCD yes")
    assert res is not None
    assert res.status == "approved"
    assert res.resolved_at is not None

    # Reset state for next sub-tests
    decision.status = "awaiting"
    decision.resolved_at = None
    await db_session.commit()

    # 2. Resolve to Rejected (e.g. ABCD reject)
    res = await permission_service.try_resolve(db_session, "ABCD reject")
    assert res is not None
    assert res.status == "rejected"

    # Reset
    decision.status = "awaiting"
    decision.resolved_at = None
    await db_session.commit()

    # 3. Handle malformed verdicts (returns None, remains awaiting)
    res = await permission_service.try_resolve(db_session, "ABCD maybe")
    assert res is None
    assert decision.status == "awaiting"

    # 4. Check case-insensitivity on code and verdict (e.g. abcd OK)
    res = await permission_service.try_resolve(db_session, "abcd OK")
    assert res is not None
    assert res.status == "approved"


@pytest.mark.asyncio
async def test_try_resolve_expired_and_cleanup(db_session):
    """Test resolution of expired decisions and bulk cron cleanup logic."""
    user = User(wa_phone="1234567890")
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)

    # 1. Create a decision that expired 5 minutes ago
    expired_time = datetime.now(timezone.utc) - timedelta(minutes=5)
    decision = PendingDecision(
        user_id=user.id,
        short_code="EXPR",
        action_type="create_event",
        proposed_action={},
        status="awaiting",
        expires_at=expired_time,
    )
    db_session.add(decision)
    await db_session.commit()

    # Trying to resolve should mark it as expired
    res = await permission_service.try_resolve(db_session, "EXPR yes")
    assert res is not None
    assert res.status == "expired"

    # 2. Create another expired decision to test bulk cron cleanup
    decision2 = PendingDecision(
        user_id=user.id,
        short_code="CLNP",
        action_type="create_event",
        proposed_action={},
        status="awaiting",
        expires_at=expired_time,
    )
    db_session.add(decision2)
    await db_session.commit()

    # Run cleanup service
    count = await permission_service.cleanup_expired(db_session)
    assert count == 1
    
    await db_session.refresh(decision2)
    assert decision2.status == "expired"
