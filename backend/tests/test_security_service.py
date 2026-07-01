from datetime import datetime, time, timezone
import pytest
from unittest.mock import MagicMock
from app.models.models import User, ChatACL, SenderACL
from app.services.security_service import security_service
from app.services.preferences_service import preferences_service

@pytest.mark.asyncio
async def test_ensure_owner_allowed(db_session):
    """Verify check owner DM is seeded as allow_all on start."""
    owner_phone = "919999999999"
    # Seed owner JID DM
    await security_service.ensure_owner_allowed(db_session, owner_phone)
    
    # Check if ChatACL has it as allow_all
    mode = await security_service.get_chat_mode(db_session, owner_phone)
    assert mode == "allow_all"
    
    # Run a second time to ensure idempotency
    await security_service.ensure_owner_allowed(db_session, owner_phone)
    mode = await security_service.get_chat_mode(db_session, owner_phone)
    assert mode == "allow_all"


@pytest.mark.asyncio
async def test_set_and_get_modes(db_session):
    """Test setting and retrieving chat modes and sender trust levels."""
    chat_id = "test_chat_jid@g.us"
    # Default mode is silent_log
    mode = await security_service.get_chat_mode(db_session, chat_id)
    assert mode == "silent_log"

    # Set mode to allow_all
    await security_service.set_chat_mode(db_session, chat_id, "allow_all", is_group=True, display_name="Test Chat")
    mode = await security_service.get_chat_mode(db_session, chat_id)
    assert mode == "allow_all"

    # Default trust is unknown
    trust = await security_service.get_sender_trust(db_session, "12345")
    assert trust == "unknown"

    # Set sender trust to vip
    await security_service.set_sender_trust(db_session, "12345", "vip", display_name="VIP Sender")
    trust = await security_service.get_sender_trust(db_session, "12345")
    assert trust == "vip"


@pytest.mark.asyncio
async def test_check_quiet_hours(db_session, mocker):
    """Verify quiet hours check, including ranges that wrap around midnight."""
    user = User(wa_phone="12345", is_owner=True)
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)

    # 1. Quiet hours not configured
    assert await security_service.check_quiet_hours(user) is False

    # 2. Quiet hours set to standard range (22:00 to 07:00), check active vs inactive times
    await preferences_service.set(user.id, "quiet_hours_start", "22:00")
    await preferences_service.set(user.id, "quiet_hours_end", "07:00")

    # Mock datetime.now(timezone.utc).astimezone() local time
    mock_now = mocker.patch("app.services.security_service.datetime")
    mock_dt = MagicMock()
    mock_now.now.return_value.astimezone.return_value = mock_dt

    # Local time is 23:30 (active)
    mock_dt.time.return_value = time(23, 30)
    assert await security_service.check_quiet_hours(user) is True

    # Local time is 12:00 (inactive)
    mock_dt.time.return_value = time(12, 0)
    assert await security_service.check_quiet_hours(user) is False

    # 3. Quiet hours set wrapping midnight (e.g. 23:00 to 06:00)
    await preferences_service.set(user.id, "quiet_hours_start", "23:00")
    await preferences_service.set(user.id, "quiet_hours_end", "06:00")
    
    # Local time is 02:00 (active)
    mock_dt.time.return_value = time(2, 0)
    assert await security_service.check_quiet_hours(user) is True

    # Local time is 22:00 (inactive)
    mock_dt.time.return_value = time(22, 0)
    assert await security_service.check_quiet_hours(user) is False

    # 4. Handle exceptions gracefully
    mock_dt.time.side_effect = Exception("Time format failure")
    assert await security_service.check_quiet_hours(user) is False


@pytest.mark.asyncio
async def test_evaluate_precedence(db_session, mocker):
    """Verify evaluate precedence: sender blocked > chat blocked > quiet hours > chat mode."""
    user = User(wa_phone="1234567890", is_owner=True)
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)

    chat_id = "test_chat"
    sender_phone = "9876543210"

    # 1. Sender blocked -> "block" (even if chat is allow_all)
    await security_service.set_sender_trust(db_session, sender_phone, "blocked")
    await security_service.set_chat_mode(db_session, chat_id, "allow_all")
    res = await security_service.evaluate(db_session, chat_id, sender_phone, user)
    assert res == "block"

    # 2. Chat blocked -> "block" (even if sender is trust normal)
    await security_service.set_sender_trust(db_session, sender_phone, "normal")
    await security_service.set_chat_mode(db_session, chat_id, "block")
    res = await security_service.evaluate(db_session, chat_id, sender_phone, user)
    assert res == "block"

    # 3. Quiet hours active -> "silent_log" (even if chat is allow_all)
    await security_service.set_chat_mode(db_session, chat_id, "allow_all")
    mocker.patch.object(security_service, "check_quiet_hours", return_value=True)
    res = await security_service.evaluate(db_session, chat_id, sender_phone, user)
    assert res == "silent_log"

    # 4. Normal chat modes when quiet hours are inactive
    mocker.patch.object(security_service, "check_quiet_hours", return_value=False)
    
    await security_service.set_chat_mode(db_session, chat_id, "allow_all")
    res = await security_service.evaluate(db_session, chat_id, sender_phone, user)
    assert res == "allow_all"

    await security_service.set_chat_mode(db_session, chat_id, "silent_log")
    res = await security_service.evaluate(db_session, chat_id, sender_phone, user)
    assert res == "silent_log"
