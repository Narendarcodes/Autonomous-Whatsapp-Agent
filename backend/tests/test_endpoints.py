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
    """Contact search matches by name or phone over the observed_contacts DB."""
    from app.api.contacts import _upsert_contacts, ContactIn

    await _upsert_contacts([
        ContactIn(phone="12025550143", name="John Doe"),
        ContactIn(phone="919999999999", name="Saketh Suman"),
    ])

    async with await get_auth_client() as ac:
        # 1. Search by name query
        resp = await ac.get("/api/contacts/search?q=John")
        assert resp.status_code == 200
        results = resp.json()
        assert len(results) == 1
        assert results[0]["wa_phone"] == "12025550143"

        # 2. Search by phone query
        resp2 = await ac.get("/api/contacts/search?q=919999")
        assert resp2.status_code == 200
        results2 = resp2.json()
        assert len(results2) == 1
        assert results2[0]["display_name"] == "Saketh Suman"


@pytest.mark.asyncio
async def test_preferences_validation_checks(db_session, monkeypatch):
    """v3: preferences accept bot_mode without Evolution verification; the
    owner phone is normalized from owner_phone and pinned to settings."""
    owner_phone = settings.OWNER_WA_PHONE.lstrip("+") or "916300354385"

    # Seed owner user
    owner = User(wa_phone=owner_phone, is_owner=True, has_permission=True)
    db_session.add(owner)
    await db_session.commit()

    async with await get_auth_client() as ac:
        # 1. Legacy-style payload with bot_mode + owner_phone saves cleanly —
        #    no 400s, no verification flow (bridge config owns mode now).
        payload = {
            "bot_name": "Jarvis",
            "timezone": "Asia/Kolkata",
            "bot_mode": "self_chat",
            "quiet_hours_start": "22:00",
            "quiet_hours_end": "07:00",
            "stt_provider": "groq",
            "tts_provider": "edge",
            "tts_voice": "Female",
            "owner_phone": owner_phone,
            "owner_name": "Custom Owner Name",
        }
        resp = await ac.post("/api/preferences", json=payload)
        assert resp.status_code == 200
        assert resp.json()["status"] == "success"

        # 2. Custom owner_name is returned on GET
        resp2 = await ac.get("/api/preferences")
        assert resp2.status_code == 200
        assert resp2.json()["owner_name"] == "Custom Owner Name"


@pytest.mark.asyncio
async def test_preferences_without_bot_mode_preserves_stored(db_session):
    """v3: bot_mode is no longer a dashboard concept (Hermes bridge config owns
    connection mode). POSTing preferences WITHOUT bot_mode must succeed and
    leave stored bot_mode/bot_phone untouched."""
    from app.services.preferences_service import preferences_service

    owner_phone = settings.OWNER_WA_PHONE.lstrip("+") or "916300354385"
    owner = User(wa_phone=owner_phone, is_owner=True, has_permission=True)
    db_session.add(owner)
    await db_session.commit()

    await preferences_service.set(owner.id, "bot_mode", "self_chat")
    await preferences_service.set(owner.id, "bot_phone", "9199998888777")

    payload = {
        "bot_name": "Jarvis",
        "timezone": "Asia/Kolkata",
        "quiet_hours_start": "22:00",
        "quiet_hours_end": "07:00",
        "stt_provider": "groq",
        "tts_provider": "edge",
        "tts_voice": "Female",
    }
    async with await get_auth_client() as ac:
        resp = await ac.post("/api/preferences", json=payload)
        assert resp.status_code == 200, resp.text

        # Stored legacy values must survive the mode-less save
        assert await preferences_service.get(owner.id, "bot_mode") == "self_chat"
        assert await preferences_service.get(owner.id, "bot_phone") == "9199998888777"

        # Other fields still saved
        resp2 = await ac.get("/api/preferences")
        assert resp2.status_code == 200
        assert resp2.json()["bot_name"] == "Jarvis"


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
async def test_sync_contacts_endpoint(db_session):
    """v3.1: /api/contacts/sync reports the observed-identities count."""
    from app.api.contacts import _upsert_contacts, ContactIn

    await _upsert_contacts([
        ContactIn(phone="12025550999", name="Observed User"),
    ])

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



