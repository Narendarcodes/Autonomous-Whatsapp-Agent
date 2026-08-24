"""#3: production must refuse to boot on known/default secrets."""
import pytest
from pydantic import ValidationError

from app.core.config import Settings

BASE = dict(
    ENVIRONMENT="production",
    ADMIN_PASSWORD="a-real-password",
    SESSION_SECRET_KEY="a-real-session-key-0123456789abcdef",
    TOKEN_ENCRYPTION_KEY="zoGXh_MbOTjg9kAmqXGZE-a4v0o8VfFpSxDcATtaUPA=",
    LITELLM_MASTER_KEY="a-real-litellm-key",
    HERMES_API_KEY="a-real-hermes-key",
    OPENWA_WEBHOOK_SECRET="a-real-webhook-secret",
)


def _settings(**overrides):
    kwargs = {**BASE, **overrides}
    return Settings(_env_file=None, **kwargs)


def test_debug_defaults_off():
    assert Settings.model_fields["DEBUG"].default is False


def test_production_with_all_secrets_boot():
    s = _settings()
    assert s.ENVIRONMENT == "production"


def test_production_refuses_default_admin_password():
    with pytest.raises(ValidationError, match="ADMIN_PASSWORD"):
        _settings(ADMIN_PASSWORD="admin123")


def test_production_refuses_default_session_key():
    with pytest.raises(ValidationError, match="SESSION_SECRET_KEY"):
        _settings(SESSION_SECRET_KEY="super_secret_session_key_naru_change_me")


def test_production_refuses_missing_webhook_secret():
    with pytest.raises(ValidationError, match="OPENWA_WEBHOOK_SECRET"):
        _settings(OPENWA_WEBHOOK_SECRET="")


def test_production_collects_all_problems():
    with pytest.raises(ValidationError) as err:
        _settings(
            ADMIN_PASSWORD="admin123",
            OPENWA_WEBHOOK_SECRET="",
            TOKEN_ENCRYPTION_KEY="",
        )
    msg = str(err.value)
    for needle in ("ADMIN_PASSWORD", "OPENWA_WEBHOOK_SECRET", "TOKEN_ENCRYPTION_KEY"):
        assert needle in msg


def test_development_still_boots_with_defaults():
    s = Settings(_env_file=None)  # all defaults, development mode
    assert s.ENVIRONMENT == "development"
