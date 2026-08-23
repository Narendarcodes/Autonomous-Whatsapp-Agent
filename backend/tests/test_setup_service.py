import pytest
from unittest.mock import AsyncMock
from app.models.models import User
from app.services.setup_service import check_setup_status, send_setup_prompt, handle_setup_command
from app.services import setup_service

@pytest.mark.asyncio
async def test_check_setup_status(db_session):
    """Test transitions of check_setup_status as user completes requirements."""
    user = User()
    
    # 1. No wa_phone -> awaiting_whatsapp
    user.wa_phone = None
    user.google_access_token_enc = None
    assert await check_setup_status(db_session, user) == "awaiting_whatsapp"

    # 2. Has wa_phone JID, but has not completed Google OAuth -> awaiting_oauth
    user.wa_phone = "919999999999"
    user.google_access_token_enc = None
    assert await check_setup_status(db_session, user) == "awaiting_oauth"

    # 3. Has completed both steps -> ready
    user.wa_phone = "919999999999"
    user.google_access_token_enc = "encrypted_access_token_val"
    assert await check_setup_status(db_session, user) == "ready"


@pytest.mark.asyncio
async def test_send_setup_prompt(mocker):
    """Verify setup instructions are formatted correctly and sent to user JID."""
    mock_send = mocker.patch.object(setup_service, "bridge_send_text", new_callable=AsyncMock)
    
    await send_setup_prompt("919999999999", "awaiting_oauth")
    mock_send.assert_called_once()
    called_phone = mock_send.call_args[0][0]
    called_msg = mock_send.call_args[0][1]
    
    assert called_phone == "919999999999"
    assert "Google OAuth required" in called_msg


@pytest.mark.asyncio
async def test_handle_setup_command(db_session):
    """Verify execution of status-related commands, case independence, and fallthroughs."""
    user = User(wa_phone="919999999999", google_access_token_enc=None)

    # 1. STATUS command (returns status description and help prompts)
    res = await handle_setup_command(db_session, user, "STATUS")
    assert "Setup Status: awaiting_oauth" in res

    # 2. OAUTH command (returns authorize redirect url link)
    res = await handle_setup_command(db_session, user, "oauth")
    assert "Visit" in res and "/oauth/authorize" in res

    # Mark user as fully authenticated and setup complete
    user.google_access_token_enc = "encrypted_access_token_val"

    # 3. STATUS command on completed profile
    res = await handle_setup_command(db_session, user, "STATUS")
    assert "Setup Status: ready" in res

    # 4. OAUTH command on completed profile
    res = await handle_setup_command(db_session, user, "OAUTH")
    assert "Google OAuth is not required right now" in res

    # 5. SETUP command on completed profile
    res = await handle_setup_command(db_session, user, "SETUP")
    assert "You're all set!" in res

    # 6. Unknown commands should fall through (returns None)
    res = await handle_setup_command(db_session, user, "HELP_COMMAND")
    assert res is None
