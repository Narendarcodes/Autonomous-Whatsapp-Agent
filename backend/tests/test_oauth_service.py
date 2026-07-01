import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch
from google.oauth2.credentials import Credentials
from app.models.models import User
from app.services.oauth_service import (
    is_token_near_expiry,
    build_authorization_url,
    exchange_code_for_tokens,
    store_user_credentials,
    load_user_credentials,
)
from app.core.security import encrypt_token, decrypt_token

def test_encryption_decryption():
    """Verify that credentials tokens can be successfully encrypted and decrypted."""
    token = "ya29.a0AfH6SMA-test-google-token-key-12345"
    enc = encrypt_token(token)
    dec = decrypt_token(enc)
    assert dec == token


def test_is_token_near_expiry_check():
    """Test boundary checks for token expiry thresholds."""
    user = User()
    
    # 1. No expiry set -> Needs refresh (True)
    user.google_token_expiry = None
    assert is_token_near_expiry(user) is True

    # 2. Expiry set far in the future -> Safe (False)
    user.google_token_expiry = datetime.now(timezone.utc) + timedelta(hours=1)
    assert is_token_near_expiry(user) is False

    # 3. Expiry within the 5-minute threshold -> Needs refresh (True)
    user.google_token_expiry = datetime.now(timezone.utc) + timedelta(minutes=4)
    assert is_token_near_expiry(user) is True


@patch("app.services.oauth_service.Flow")
def test_build_authorization_url(mock_flow):
    """Verify state parameters and authorization url construction."""
    mock_flow_instance = MagicMock()
    mock_flow_instance.authorization_url.return_value = ("http://accounts.google.com/auth-test", "state_val")
    mock_flow_instance.code_verifier = "mock_verifier_xyz"
    mock_flow.from_client_config.return_value = mock_flow_instance

    url, code_verifier = build_authorization_url("random_state_string_xyz")
    assert url == "http://accounts.google.com/auth-test"
    assert code_verifier == "mock_verifier_xyz"
    mock_flow_instance.authorization_url.assert_called_once_with(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
        state="random_state_string_xyz",
    )


@patch("app.services.oauth_service.Flow")
def test_exchange_code_for_tokens(mock_flow):
    """Test token retrieval using OAuth code exchange."""
    mock_flow_instance = MagicMock()
    mock_creds = MagicMock(spec=Credentials)
    mock_flow_instance.credentials = mock_creds
    mock_flow.from_client_config.return_value = mock_flow_instance

    creds = exchange_code_for_tokens("code_from_callback", code_verifier="mock_verifier_xyz")
    assert creds == mock_creds
    assert mock_flow_instance.code_verifier == "mock_verifier_xyz"
    mock_flow_instance.fetch_token.assert_called_once_with(code="code_from_callback")


@pytest.mark.asyncio
async def test_store_user_credentials(db_session):
    """Verify that credentials are stored encrypted in the user table."""
    user = User(wa_phone="12345")
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)

    mock_creds = MagicMock(spec=Credentials)
    mock_creds.token = "access_token_abc"
    mock_creds.refresh_token = "refresh_token_def"
    mock_creds.expiry = datetime.now(timezone.utc) + timedelta(hours=1)

    await store_user_credentials(db_session, user, mock_creds)

    # Assert values are stored encrypted
    assert user.google_access_token_enc is not None
    assert decrypt_token(user.google_access_token_enc) == "access_token_abc"
    assert user.google_refresh_token_enc is not None
    assert decrypt_token(user.google_refresh_token_enc) == "refresh_token_def"
    assert user.google_token_expiry is not None


@pytest.mark.asyncio
async def test_load_user_credentials_with_refresh(db_session, mocker):
    """Test loading credentials, verifying auto-refresh trigger when expired."""
    user = User(wa_phone="12345")
    user.google_access_token_enc = encrypt_token("expired_access_token")
    user.google_refresh_token_enc = encrypt_token("valid_refresh_token")
    # Expired 1 hour ago
    user.google_token_expiry = datetime.now(timezone.utc) - timedelta(hours=1)
    
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)

    # Patch GoogleRequest and Google Credentials refresh call
    mocker.patch("app.services.oauth_service.GoogleRequest")
    mock_refresh = mocker.patch("google.oauth2.credentials.Credentials.refresh")
    
    creds = await load_user_credentials(user)
    assert creds is not None
    # Expiry refresh should be triggered
    mock_refresh.assert_called_once()
    assert creds.refresh_token == "valid_refresh_token"
