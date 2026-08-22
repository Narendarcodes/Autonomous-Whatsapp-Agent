"""Tests for Group Privacy Mode — Unit 1 (service layer).

Ensures the agent never leaks owner-sensitive info when replying in groups:
1. is_group_chat detection
2. Privacy directive contains hard guardrails
3. redact() masks emails, phone numbers, and long numeric tokens
"""
import pytest

from app.services.group_privacy_service import (
    build_group_privacy_directive,
    is_group_chat,
    redact,
)


class TestIsGroupChat:
    def test_group_jid_detected(self):
        assert is_group_chat("120363021212099999@g.us") is True

    def test_dm_phone_not_group(self):
        assert is_group_chat("919876543210") is False
        assert is_group_chat("+919876543210") is False
        assert is_group_chat("919876543210@s.whatsapp.net") is False

    def test_empty_and_none_safe(self):
        assert is_group_chat("") is False
        assert is_group_chat(None) is False


class TestPrivacyDirective:
    def test_directive_mentions_no_private_data(self):
        d = build_group_privacy_directive()
        assert "calendar" in d.lower()
        assert "email" in d.lower()
        # Must instruct redirecting sensitive detail to DM
        assert "dm" in d.lower() or "private" in d.lower()

    def test_directive_is_nonempty_string(self):
        assert isinstance(build_group_privacy_directive(), str)
        assert len(build_group_privacy_directive()) > 50


class TestRedact:
    def test_masks_email(self):
        out = redact("contact narendar@omniwa.app for details")
        assert "narendar@omniwa.app" not in out
        assert "[REDACTED]" in out

    def test_masks_phone_number(self):
        out = redact("call me at +919876543210 please")
        assert "919876543210" not in out

    def test_masks_long_numeric_token(self):
        out = redact("your code is 4839201756473820")
        assert "4839201756473820" not in out

    def test_plain_text_untouched(self):
        text = "The meeting is at 5pm tomorrow."
        assert redact(text) == text

    def test_empty_text_safe(self):
        assert redact("") == ""
        assert redact(None) in ("", None)

    def test_short_numbers_kept(self):
        """Everyday numbers (times, small counts) must NOT be masked."""
        text = "Meet 10 people at 5pm room 402"
        assert redact(text) == text
