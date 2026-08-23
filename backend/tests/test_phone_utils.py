"""Characterization tests for phone_utils (moved out of the deleted Evolution
client). Cover owner country inference, E.164 validation/normalisation and
group-JID handling."""
from app.core.config import settings
from app.services.phone_utils import (
    get_owner_country_code,
    normalize_phone_number,
    validate_phone_number,
)


def test_owner_country_code_from_settings():
    digits = "".join(ch for ch in settings.OWNER_WA_PHONE if ch.isdigit())
    if len(digits) > 10:
        assert get_owner_country_code() == digits[:-10]
    else:
        assert get_owner_country_code() == "91"


def test_validate_indian_mobile():
    result = validate_phone_number("9876543210")
    assert result["is_valid"] is True
    assert result["digits"] == "919876543210"
    assert result["country_code"] == "91"
    assert result["error"] is None


def test_validate_international_with_plus():
    result = validate_phone_number("+12025550144")
    assert result["is_valid"] is True
    assert result["digits"] == "12025550144"


def test_validate_invalid_number():
    result = validate_phone_number("12345")
    assert result["is_valid"] is False
    assert result["digits"] == ""
    assert result["error"]


def test_validate_empty():
    result = validate_phone_number("")
    assert result["is_valid"] is False


def test_validate_group_jid_passthrough():
    result = validate_phone_number("120312345678-987654321@g.us")
    assert result["is_valid"] is True
    assert result.get("is_group") is True
    assert result["digits"] == "120312345678-987654321"


def test_normalize_returns_digits_or_empty():
    assert normalize_phone_number("+919876543210") == "919876543210"
    assert normalize_phone_number("not-a-phone") == ""
    assert normalize_phone_number(None) == ""
