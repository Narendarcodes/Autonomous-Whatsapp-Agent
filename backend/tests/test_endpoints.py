import pytest
from httpx import ASGITransport, AsyncClient
from app.main import app
from app.core.config import settings
from app.models.models import User, ChatACL
from app.db.redis_client import get_redis

async def get_auth_client():
    from app.db.redis_client import cache_set
    session_id = "test-session-id"
    await cache_set(f"session:{session_id}", "1", ttl_seconds=300)
    cookies = {"naru_session": session_id}
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test", cookies=cookies)

@pytest.mark.asyncio
async def test_system_status_endpoint():
    """Verify host metrics API returns CPU, RAM, and disk status."""
    async with await get_auth_client() as ac:
        resp = await ac.get("/api/system-status")
        assert resp.status_code == 200
        data = resp.json()
        assert "cpu" in data
        assert "ram" in data
        assert "disk" in data
        assert data["status"] in ("online", "error")

@pytest.mark.asyncio
async def test_access_control_permissions(db_session):
    """Verify user registration, permission grants, and revocations."""
    # Seed owner user
    owner_phone = settings.OWNER_WA_PHONE.lstrip("+")
    if not owner_phone:
        owner_phone = "916300354385" # fallback for test runner if settings has no owner phone
    owner = User(wa_phone=owner_phone, is_owner=True, has_permission=True)
    db_session.add(owner)

    # Seed test user
    test_phone = "919999999999"
    user = User(wa_phone=test_phone, is_owner=False, has_permission=False)
    db_session.add(user)
    await db_session.commit()

    async with await get_auth_client() as ac:
        # 1. Grant permission
        grant_resp = await ac.post(f"/permissions/grant?phone={test_phone}")
        assert grant_resp.status_code == 200
        assert grant_resp.json()["status"] == "granted"

        # Verify DB updated
        await db_session.refresh(user)
        assert user.has_permission is True

        # 2. Revoke permission
        revoke_resp = await ac.post(f"/permissions/revoke?phone={test_phone}")
        assert revoke_resp.status_code == 200
        assert revoke_resp.json()["status"] == "revoked"

        # Verify DB updated
        await db_session.refresh(user)
        assert user.has_permission is False

        # 3. Grant permission to a non-existing user (should auto-create and whitelist)
        new_phone = "918888888888"
        grant_new_resp = await ac.post(f"/permissions/grant?phone={new_phone}")
        assert grant_new_resp.status_code == 200
        assert grant_new_resp.json()["status"] == "granted"

        # Verify DB auto-created and whitelisted the user
        from sqlalchemy import select
        new_user_res = await db_session.execute(select(User).where(User.wa_phone == new_phone))
        new_user = new_user_res.scalar_one_or_none()
        assert new_user is not None
        assert new_user.has_permission is True
        assert new_user.display_name == f"User {new_phone[-4:]}"

@pytest.mark.asyncio
async def test_webhook_signature_verification():
    """Test signature rejection for unauthorized payloads."""
    payload = {"event": "messages.upsert", "data": {}}
    headers = {"X-Evolution-Signature": "invalid_hash_value"}
    
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post("/webhook/openwa", json=payload, headers=headers)
        # Should return 401 Unauthorized if settings.OPENWA_WEBHOOK_SECRET is set
        if settings.OPENWA_WEBHOOK_SECRET:
            assert resp.status_code == 401
        else:
            assert resp.status_code in (200, 400) # In dev mode (no secret) signature is bypassed

@pytest.mark.asyncio
async def test_rate_limiting():
    """Test Redis-backed message request rate limiter."""
    from app.db.redis_client import check_rate_limit
    sender = "test_sender_123"
    
    # Clean redis state for test sender
    r = await get_redis()
    await r.delete(f"rl:{sender}")
    
    # Request up to the limit
    for _ in range(settings.RATE_LIMIT_REQUESTS):
        allowed = await check_rate_limit(sender)
        assert allowed is True
        
    # Next request must be rate-limited
    blocked = await check_rate_limit(sender)
    assert blocked is False

@pytest.mark.asyncio
async def test_unauthenticated_api_redirect():
    """Verify visiting /dashboard or /setup without cookies redirects to /login."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/dashboard")
        assert resp.status_code == 302
        assert resp.headers["location"] == "/login"

        resp2 = await ac.get("/setup")
        assert resp2.status_code == 302
        assert resp2.headers["location"] == "/login"

@pytest.mark.asyncio
async def test_login_success():
    """Verify submitting the correct password returns a session cookie and redirects."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # Wrong password redirects to /login?error=true
        resp = await ac.post("/login", data={"password": "wrong_password"})
        assert resp.status_code == 303
        assert resp.headers["location"] == "/login?error=true"

        # Correct password redirects to /dashboard and sets cookies
        resp2 = await ac.post("/login", data={"password": settings.ADMIN_PASSWORD})
        assert resp2.status_code == 303
        assert resp2.headers["location"] == "/dashboard"
        assert "naru_session" in resp2.cookies

@pytest.mark.asyncio
async def test_reply_context_extraction(db_session):
    """Verify webhook parser correctly extracts quoted messages."""
    from app.api.webhooks import _parse_evolution_event
    payload = {
        "event": "messages.upsert",
        "instanceId": "naru-instance",
        "data": {
            "key": {
                "remoteJid": "919999999999@s.whatsapp.net",
                "fromMe": False,
                "id": "MSG12345"
            },
            "message": {
                "extendedTextMessage": {
                    "text": "Hello, this is my response!",
                    "contextInfo": {
                        "quotedMessage": {
                            "conversation": "What is your name?"
                        },
                        "participant": "919999999999@s.whatsapp.net"
                    }
                }
            },
            "messageTimestamp": 1672531199,
            "pushName": "Test User"
        }
    }
    parsed = await _parse_evolution_event(payload)
    assert parsed is not None
    assert parsed["message_text"] == "Hello, this is my response!"
    assert parsed["quoted_text"] == "What is your name?"
    assert parsed["sender_phone"] == "919999999999"

@pytest.mark.asyncio
async def test_quiet_hours_validation():
    """Verify that PreferencesPayload validator rejects invalid time formats."""
    from app.api.setup import PreferencesPayload
    from pydantic import ValidationError

    # Valid times work fine
    PreferencesPayload(
        bot_name="Jarvis",
        timezone="Asia/Kolkata",
        bot_mode="self_chat",
        quiet_hours_start="22:00",
        quiet_hours_end="07:00",
        stt_provider="groq",
        tts_provider="edge",
        tts_voice="Female"
    )

    # Invalid start time throws ValidationError
    with pytest.raises(ValidationError):
        PreferencesPayload(
            bot_name="Jarvis",
            timezone="Asia/Kolkata",
            bot_mode="self_chat",
            quiet_hours_start="25:88",  # invalid
            quiet_hours_end="07:00",
            stt_provider="groq",
            tts_provider="edge",
            tts_voice="Female"
        )

@pytest.mark.asyncio
async def test_oauth_callback_expired_state():
    """Verify that visiting /oauth/callback with an expired/invalid state returns the friendly HTML error."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/oauth/callback?code=mock_code&state=expired_state_value")
        assert resp.status_code == 400
        assert "text/html" in resp.headers["content-type"]
        assert "Connection Expired" in resp.text
        assert "Return to Dashboard" in resp.text


@pytest.mark.asyncio
async def test_contacts_search(db_session):
    """Verify that contact search autocomplete matches by name or phone."""
    from app.db.redis_client import cache_set
    import json
    
    # Mock contacts list
    mock_contacts = [
        {"phone": "12025550143", "name": "John Doe", "jid": "12025550143@s.whatsapp.net"},
        {"phone": "919999999999", "name": "Saketh Suman", "jid": "919999999999@s.whatsapp.net"}
    ]
    await cache_set("whatsapp:contacts_cache", json.dumps(mock_contacts), ttl_seconds=300)
    
    # 1. Search by name query
    async with await get_auth_client() as ac:
        resp = await ac.get("/api/contacts/search?q=John")
        assert resp.status_code == 200
        results = resp.json()
        assert len(results) == 1
        assert results[0]["phone"] == "12025550143"
        
        # 2. Search by phone query
        resp2 = await ac.get("/api/contacts/search?q=919999")
        assert resp2.status_code == 200
        results2 = resp2.json()
        assert len(results2) == 1
        assert results2[0]["name"] == "Saketh Suman"


@pytest.mark.asyncio
async def test_preferences_validation_checks(db_session, monkeypatch):
    """Verify preference validation constraints for dual phone config against Evolution API active connection."""
    from app.services.whatsapp_service import whatsapp_service
    
    owner_phone = settings.OWNER_WA_PHONE.lstrip("+")
    if not owner_phone:
        owner_phone = "916300354385"
    
    # Seed owner user
    owner = User(wa_phone=owner_phone, is_owner=True, has_permission=True)
    db_session.add(owner)
    await db_session.commit()
    
    # Mock connected bot phone number
    async def mock_get_bot_phone():
        return "12025550144"
    
    monkeypatch.setattr(whatsapp_service, "get_bot_phone", mock_get_bot_phone)
    
    async with await get_auth_client() as ac:
        # 1. Validation fails: bot_phone is missing/empty in dual_number mode
        payload_fail = {
            "bot_name": "Jarvis",
            "timezone": "Asia/Kolkata",
            "bot_mode": "dual_number",
            "quiet_hours_start": "22:00",
            "quiet_hours_end": "07:00",
            "stt_provider": "groq",
            "tts_provider": "edge",
            "tts_voice": "Female",
            "bot_phone": ""
        }
        resp = await ac.post("/api/preferences", json=payload_fail)
        assert resp.status_code == 400
        assert "Agent Phone must be configured" in resp.json()["detail"]
        
        # 2. Providing a NEW bot_phone triggers verification_pending (whatsapp DM confirmation required)
        payload_new_bot = {
            "bot_name": "Jarvis",
            "timezone": "Asia/Kolkata",
            "bot_mode": "dual_number",
            "quiet_hours_start": "22:00",
            "quiet_hours_end": "07:00",
            "stt_provider": "groq",
            "tts_provider": "edge",
            "tts_voice": "Female",
            "bot_phone": "12025550222"
        }
        resp2 = await ac.post("/api/preferences", json=payload_new_bot)
        assert resp2.status_code == 200
        # Newly submitted bot_phone triggers a WhatsApp verification DM; pending until confirmed
        assert resp2.json()["status"] in ("verification_pending", "success")
        
        # Verify settings OWNER_WA_PHONE is updated to the connected phone
        assert settings.OWNER_WA_PHONE == "12025550144"

        # 3. POST with the same bot_phone again (no change) + custom owner_name → succeeds immediately
        payload_same_bot = {
            "bot_name": "Jarvis",
            "timezone": "Asia/Kolkata",
            "bot_mode": "dual_number",
            "quiet_hours_start": "22:00",
            "quiet_hours_end": "07:00",
            "stt_provider": "groq",
            "tts_provider": "edge",
            "tts_voice": "Female",
            "bot_phone": "12025550222",
            "owner_name": "Custom Owner Name"
        }
        # Manually force the preference so re-submission detects "no change" and skips verification
        from app.services.preferences_service import preferences_service
        await preferences_service.set(owner.id, "bot_phone", "12025550222")
        resp3 = await ac.post("/api/preferences", json=payload_same_bot)
        assert resp3.status_code == 200
        # Same number already configured → skips verification, saves successfully
        assert resp3.json()["status"] == "success"
        
        # 4. Verify custom owner_name is returned on GET
        resp4 = await ac.get("/api/preferences")
        assert resp4.status_code == 200
        assert resp4.json()["owner_name"] == "Custom Owner Name"


@pytest.mark.asyncio
async def test_agent_phone_endpoints(db_session, monkeypatch):
    """Test the agent phone linking (QR and polling status) endpoints."""
    from sqlalchemy import select
    from app.services.agent_instance_service import agent_instance_service
    from app.services.whatsapp_service import whatsapp_service
    from app.services.preferences_service import preferences_service
    
    owner_phone = settings.OWNER_WA_PHONE.lstrip("+")
    if not owner_phone:
        owner_phone = "916300354385"
        
    # Seed owner user
    owner = User(wa_phone=owner_phone, is_owner=True, has_permission=True)
    db_session.add(owner)
    await db_session.commit()
    
    # Mock agent instance service calls
    async def mock_create_agent_instance():
        return "mock_qr_code_base64"
    
    async def mock_get_status_connecting():
        return {"state": "connecting", "qr": "mock_qr_code_base64"}
        
    async def mock_get_status_open():
        return {"state": "open"}
        
    async def mock_get_agent_phone():
        return "12025550333"

    monkeypatch.setattr(agent_instance_service, "create_agent_instance", mock_create_agent_instance)
    monkeypatch.setattr(whatsapp_service, "send_text", lambda *args, **kwargs: None)
    
    async with await get_auth_client() as ac:
        # 1. Invalid agent phone number request
        resp_invalid = await ac.post("/api/agent-phone/request", json={"phone": "123"})
        assert resp_invalid.status_code == 400
        assert "Invalid phone" in resp_invalid.json()["detail"] or "invalid" in resp_invalid.json()["detail"].lower()

        # 2. Valid agent phone request
        resp_valid = await ac.post("/api/agent-phone/request", json={"phone": "12025550333"})
        assert resp_valid.status_code == 200
        data = resp_valid.json()
        assert data["status"] == "qr_ready"
        assert data["qr"] == "mock_qr_code_base64"
        assert data["phone"] == "12025550333"
        
        # 3. Poll while state is still connecting
        monkeypatch.setattr(agent_instance_service, "get_agent_instance_status", mock_get_status_connecting)
        resp_status1 = await ac.get("/api/agent-phone/qr-status")
        assert resp_status1.status_code == 200
        assert resp_status1.json()["state"] == "connecting"
        assert resp_status1.json()["qr"] == "mock_qr_code_base64"
        
        # 4. Poll when connected
        monkeypatch.setattr(agent_instance_service, "get_agent_instance_status", mock_get_status_open)
        monkeypatch.setattr(agent_instance_service, "get_agent_phone", mock_get_agent_phone)
        
        resp_status2 = await ac.get("/api/agent-phone/qr-status")
        assert resp_status2.status_code == 200
        assert resp_status2.json()["state"] == "open"
        assert resp_status2.json()["phone"] == "12025550333"
        
        # Check preferences updated
        bot_phone = await preferences_service.get(owner.id, "bot_phone")
        bot_mode = await preferences_service.get(owner.id, "bot_mode")
        assert bot_phone == "12025550333"
        assert bot_mode == "dual_number"
        
        # Check whitelisted in database
        result = await db_session.execute(select(User).where(User.wa_phone == "12025550333"))
        agent_user = result.scalar_one_or_none()
        assert agent_user is not None
        assert agent_user.has_permission is True
        assert agent_user.display_name == "Agent Chat"

        # 5. Cancel agent setup (reverts to self_chat)
        async def mock_delete_agent_instance_success():
            return True
            
        monkeypatch.setattr(agent_instance_service, "delete_agent_instance", mock_delete_agent_instance_success)
        resp_cancel = await ac.post("/api/agent-phone/cancel")
        assert resp_cancel.status_code == 200
        assert resp_cancel.json()["status"] == "success"
        
        # Verify preferences reverted
        bot_phone_after = await preferences_service.get(owner.id, "bot_phone")
        bot_mode_after = await preferences_service.get(owner.id, "bot_mode")
        assert bot_phone_after == ""
        assert bot_mode_after == "self_chat"


@pytest.mark.asyncio
async def test_reset_permissions_endpoint(db_session):
    """Verify that resetting permissions deletes all users except owner and connected agent chat."""
    from app.services.preferences_service import preferences_service
    from sqlalchemy import select

    owner_phone = settings.OWNER_WA_PHONE.lstrip("+")
    if not owner_phone:
        owner_phone = "916300354385"

    # Seed owner user
    owner = User(wa_phone=owner_phone, is_owner=True, has_permission=True)
    db_session.add(owner)

    # Seed normal whitelisted contact
    whitelist_phone = "12025550222"
    whitelisted_user = User(wa_phone=whitelist_phone, is_owner=False, has_permission=True, display_name="John Doe")
    db_session.add(whitelisted_user)

    # Seed agent user
    agent_phone = "12025550333"
    agent_user = User(wa_phone=agent_phone, is_owner=False, has_permission=True, display_name="Agent Chat")
    db_session.add(agent_user)

    await db_session.commit()

    # Configure bot_phone to agent_phone in preferences
    await preferences_service.set(owner.id, "bot_phone", agent_phone)

    # Call reset permissions endpoint
    async with await get_auth_client() as ac:
        resp = await ac.post("/permissions/reset")
        assert resp.status_code == 200
        assert resp.json()["status"] == "success"

        # Verify that whitelist contact was deleted
        result = await db_session.execute(select(User).where(User.wa_phone == whitelist_phone))
        assert result.scalar_one_or_none() is None

        # Verify that owner is NOT deleted
        result_owner = await db_session.execute(select(User).where(User.wa_phone == owner_phone))
        assert result_owner.scalar_one_or_none() is not None

        # Verify that agent is NOT deleted
        result_agent = await db_session.execute(select(User).where(User.wa_phone == agent_phone))
        assert result_agent.scalar_one_or_none() is not None


@pytest.mark.asyncio
async def test_delete_permission_endpoint(db_session):
    """Verify that deleting a specific user contact deletes it from DB and blocks owner/agent deletion."""
    from sqlalchemy import select
    from app.services.preferences_service import preferences_service

    owner_phone = settings.OWNER_WA_PHONE.lstrip("+")
    if not owner_phone:
        owner_phone = "916300354385"

    # Seed owner user
    owner = User(wa_phone=owner_phone, is_owner=True, has_permission=True)
    db_session.add(owner)

    # Seed whitelisted contact
    whitelist_phone = "12025550999"
    whitelisted_user = User(wa_phone=whitelist_phone, is_owner=False, has_permission=True, display_name="Jane Doe")
    db_session.add(whitelisted_user)

    # Seed agent user
    agent_phone = "12025550888"
    agent_user = User(wa_phone=agent_phone, is_owner=False, has_permission=True, display_name="Agent Chat")
    db_session.add(agent_user)

    await db_session.commit()

    # Configure bot_phone in preferences
    await preferences_service.set(owner.id, "bot_phone", agent_phone)

    async with await get_auth_client() as ac:
        # 1. Deleting normal user succeeds
        resp = await ac.post(f"/permissions/delete?phone={whitelist_phone}")
        assert resp.status_code == 200
        assert resp.json()["status"] == "deleted"

        # Verify DB deletion
        res = await db_session.execute(select(User).where(User.wa_phone == whitelist_phone))
        assert res.scalar_one_or_none() is None

        # 2. Deleting owner fails with 400
        resp_owner = await ac.post(f"/permissions/delete?phone={owner_phone}")
        assert resp_owner.status_code == 400

        # 3. Deleting active agent fails with 400
        resp_agent = await ac.post(f"/permissions/delete?phone={agent_phone}")
        assert resp_agent.status_code == 400


@pytest.mark.asyncio
async def test_dynamic_owner_resolution(db_session, monkeypatch):
    """Verify that polling qr-status dynamically syncs the owner setting and DB record."""
    from app.services.whatsapp_service import whatsapp_service
    from sqlalchemy import select

    # Mock connected bot phone number
    async def mock_get_bot_phone():
        return "919999999999"

    async def mock_instance_status(*args):
        return {"instance": {"state": "open"}}

    async def mock_get_profile_pic(*args):
        return None

    async def mock_sync_contacts(*args):
        return []

    monkeypatch.setattr(whatsapp_service, "get_bot_phone", mock_get_bot_phone)
    monkeypatch.setattr(whatsapp_service, "instance_status", mock_instance_status)
    monkeypatch.setattr(whatsapp_service, "get_profile_picture", mock_get_profile_pic)
    monkeypatch.setattr(whatsapp_service, "sync_contacts", mock_sync_contacts)

    async with await get_auth_client() as ac:
        resp = await ac.get("/setup/qr-status")
        assert resp.status_code == 200

        # Check settings updated
        assert settings.OWNER_WA_PHONE == "919999999999"

        # Check DB updated
        result = await db_session.execute(select(User).where(User.wa_phone == "919999999999"))
        owner_user = result.scalar_one_or_none()
        assert owner_user is not None
        assert owner_user.is_owner is True
        assert owner_user.has_permission is True


@pytest.mark.asyncio
async def test_sync_contacts_endpoint(db_session, monkeypatch):
    """Verify that calling POST /api/contacts/sync triggers sync and returns count."""
    from app.services.whatsapp_service import whatsapp_service
    
    # Mock contacts sync return
    async def mock_sync_contacts():
        return [{"phone": "12025550999", "name": "Sync User", "jid": "12025550999@s.whatsapp.net"}]
        
    monkeypatch.setattr(whatsapp_service, "sync_contacts", mock_sync_contacts)
    
    async with await get_auth_client() as ac:
        resp = await ac.post("/api/contacts/sync")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "success"
        assert data["count"] == 1


@pytest.mark.asyncio
async def test_search_contacts_empty_query(db_session):
    """Verify that searching with an empty query returns an empty list immediately."""
    async with await get_auth_client() as ac:
        resp = await ac.get("/api/contacts/search?q=")
        assert resp.status_code == 200
        assert resp.json() == []



