import pytest
from sqlalchemy import select
from app.models.models import User, ChatACL, SenderACL, UserPreference, AuditLog
from app.services.command_parser import handle_command
from app.services.security_service import security_service

USER_UUID = "11111111-1111-1111-1111-111111111111"

async def seed_user(db_session):
    """Helper to seed the test user in the current session transaction."""
    user = User(id=USER_UUID, wa_phone="919999999999", is_owner=True)
    db_session.add(user)
    await db_session.commit()


@pytest.mark.asyncio
async def test_command_parser_case_and_fallthrough(db_session):
    """Verify non-command messages fall through and command triggers ignore case-sensitivity."""
    await seed_user(db_session)
    user_id = USER_UUID
    
    # 1. Non-command text should return None (fallthrough to LLM)
    res = await handle_command(db_session, user_id, "hello bot")
    assert res is None

    # 2. Case insensitivity check
    res_allow = await handle_command(db_session, user_id, "/ALLOW 12345")
    assert "Allowed." in res_allow
    
    acl = await security_service.get_chat_mode(db_session, "12345")
    assert acl == "allow_all"


@pytest.mark.asyncio
async def test_command_parser_acl_modes(db_session):
    """Test /allow, /block, and /silence commands."""
    await seed_user(db_session)
    user_id = USER_UUID
    target = "919999999999"
    
    # Test Allow
    res = await handle_command(db_session, user_id, f"/allow {target}")
    assert "Allowed." in res
    assert await security_service.get_chat_mode(db_session, target) == "allow_all"

    # Test Block
    res = await handle_command(db_session, user_id, f"/block {target}")
    assert "Blocked." in res
    assert await security_service.get_chat_mode(db_session, target) == "block"

    # Test Silence
    res = await handle_command(db_session, user_id, f"/silence {target}")
    assert "Silenced." in res
    assert await security_service.get_chat_mode(db_session, target) == "silent_log"

    # Edge Case: Check usage prompts when arguments are missing
    assert "Usage" in await handle_command(db_session, user_id, "/allow")
    assert "Usage" in await handle_command(db_session, user_id, "/block")
    assert "Usage" in await handle_command(db_session, user_id, "/silence")


@pytest.mark.asyncio
async def test_command_parser_trust(db_session):
    """Test /trust and /untrust commands."""
    await seed_user(db_session)
    user_id = USER_UUID
    phone = "12345"
    
    # Trust sender JID as VIP
    res = await handle_command(db_session, user_id, f"/trust {phone}")
    assert "VIP" in res.upper()
    assert await security_service.get_sender_trust(db_session, phone) == "vip"

    # Untrust sender JID back to normal
    res = await handle_command(db_session, user_id, f"/untrust {phone}")
    assert "NORMAL" in res.upper()
    assert await security_service.get_sender_trust(db_session, phone) == "normal"

    # Edge Case: Check usage prompts
    assert "Usage" in await handle_command(db_session, user_id, "/trust")


@pytest.mark.asyncio
async def test_command_parser_quiet(db_session):
    """Test quiet hours configuration commands."""
    await seed_user(db_session)
    user_id = USER_UUID
    
    # 1. Valid quiet hours format
    res = await handle_command(db_session, user_id, "/quiet 22:00-07:00")
    assert "Quiet hours set: 22:00 → 07:00" in res

    # 2. Invalid quiet hours format (e.g. invalid minutes/hours or format)
    res_invalid = await handle_command(db_session, user_id, "/quiet 25:99-07:00")
    assert "Usage: /quiet" in res_invalid

    res_invalid_format = await handle_command(db_session, user_id, "/quiet tomorrow")
    assert "Usage: /quiet" in res_invalid_format


@pytest.mark.asyncio
async def test_command_parser_memory_and_voice(db_session):
    """Test chat settings toggles like memory and voice transcription."""
    await seed_user(db_session)
    user_id = USER_UUID
    chat_id = "919999999999"

    # 1. Attempt toggle when JID is not yet seeded in ChatACL
    res_err = await handle_command(db_session, user_id, f"/memory-on {chat_id}")
    assert "not in ACL yet" in res_err

    # 2. Seed in ACL first
    await security_service.set_chat_mode(db_session, chat_id, "allow_all")

    # 3. Toggle memory
    res_mem_on = await handle_command(db_session, user_id, f"/memory-on {chat_id}")
    assert "Memory enabled" in res_mem_on
    
    res_mem_off = await handle_command(db_session, user_id, f"/memory-off {chat_id}")
    assert "Memory disabled" in res_mem_off

    # 4. Toggle voice transcription
    res_voice_on = await handle_command(db_session, user_id, f"/voice-on {chat_id}")
    assert "Voice transcription enabled" in res_voice_on

    res_voice_off = await handle_command(db_session, user_id, f"/voice-off {chat_id}")
    assert "Voice transcription disabled" in res_voice_off

    # Edge cases
    assert "Usage" in await handle_command(db_session, user_id, "/memory-on")
    assert "Usage" in await handle_command(db_session, user_id, "/voice-on")


@pytest.mark.asyncio
async def test_command_parser_set_and_show(db_session):
    """Test user preference settings mutations and listing logs."""
    await seed_user(db_session)

    # 1. Set key/value preference
    res_set = await handle_command(db_session, USER_UUID, "/set bot_mode self_chat")
    assert "Preference saved: bot_mode = self_chat" in res_set

    # 2. Set with invalid choice for bot_relation
    res_set_invalid = await handle_command(db_session, USER_UUID, "/set bot_mode invalid_mode")
    assert "Error: bot_mode must be 'self_chat' or 'dual_number'" in res_set_invalid

    # 3. Set malformed options
    res_set_missing = await handle_command(db_session, USER_UUID, "/set bot_name")
    assert "Usage: /set" in res_set_missing

    # 4. Show preferences
    res_show_prefs = await handle_command(db_session, USER_UUID, "/show prefs")
    assert "bot_mode = self_chat" in res_show_prefs

    # Seed at least one ChatACL entry so show acl returns list instead of empty message
    await security_service.set_chat_mode(db_session, "12345", "allow_all")

    # 5. Show acl listing
    res_show_acl = await handle_command(db_session, USER_UUID, "/show acl")
    assert "Chat ACL:" in res_show_acl

    # 6. Show audit logging entries
    res_show_audit = await handle_command(db_session, USER_UUID, "/show audit")
    assert "Last" in res_show_audit or "No audit entries" in res_show_audit

    # 7. Configure assistance description
    res_configure = await handle_command(db_session, USER_UUID, "/configure")
    assert "AI Assistant Chat Configuration" in res_configure


