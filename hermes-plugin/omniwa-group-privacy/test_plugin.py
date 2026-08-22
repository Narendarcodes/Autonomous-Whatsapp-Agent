"""Unit tests for the omniwa-group-privacy Hermes plugin.

Run inside the hermes container:
  /opt/hermes/.venv/bin/python -m pytest /opt/data/plugins/omniwa-group-privacy/test_plugin.py -q
"""
import importlib
import sys
from pathlib import Path

import pytest

PLUGIN_DIR = Path(__file__).resolve().parent
if str(PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(PLUGIN_DIR))

# Import fresh regardless of how the gateway may have loaded it
sys.modules.pop("omniwa_group_privacy_testee", None)
spec = importlib.util.spec_from_file_location(
    "omniwa_group_privacy_testee", PLUGIN_DIR / "__init__.py"
)
plugin = importlib.util.module_from_spec(spec)
spec.loader.exec_module(plugin)

from gateway.session_context import clear_session_vars, set_session_vars


class FakeCtx:
    def __init__(self):
        self.hooks = {}

    def register_hook(self, name, fn):
        self.hooks[name] = fn


@pytest.fixture
def hooks():
    ctx = FakeCtx()
    plugin.register(ctx)
    assert set(ctx.hooks) == {"pre_llm_call", "transform_llm_output"}
    return ctx.hooks


def _group_env():
    return set_session_vars(
        platform="whatsapp",
        chat_id="120363021212099999@g.us",
        chat_name="Test Group",
        user_id="919999999999",
        session_key="agent:main:whatsapp:group:120363021212099999@g.us:919999999999",
    )


def _dm_env():
    return set_session_vars(
        platform="whatsapp",
        chat_id="919876543210",
        chat_name="Owner DM",
        user_id="919876543210",
        session_key="agent:main:whatsapp:dm:919876543210",
    )


def test_redact_masks_email_phone_and_long_numbers():
    text = "Mail narendar@omniwa.app or call +91 98765 43210, ref 1234567890123"
    out = plugin.redact_group_text(text)
    assert out is not None
    assert "narendar@omniwa.app" not in out
    assert "98765" not in out
    assert "1234567890123" not in out
    assert "[REDACTED]" in out


def test_redact_passthrough_returns_none_when_clean():
    assert plugin.redact_group_text("All clear, meeting soon!") is None
    assert plugin.redact_group_text(None) is None
    assert plugin.redact_group_text("") is None


def test_group_chat_detected_by_chat_id():
    tokens = _group_env()
    try:
        assert plugin._active_chat_is_whatsapp_group() is True
    finally:
        clear_session_vars(tokens)


def test_dm_not_treated_as_group():
    tokens = _dm_env()
    try:
        assert plugin._active_chat_is_whatsapp_group() is False
    finally:
        clear_session_vars(tokens)


def test_no_session_context_defaults_to_non_group():
    # No contextvars set (CLI/cron/api_server paths) — must stay permissive-off.
    assert plugin._active_chat_is_whatsapp_group() is False


def test_pre_llm_call_injects_directive_only_for_groups(hooks):
    tokens = _group_env()
    try:
        result = hooks["pre_llm_call"](user_message="@bot what's on my calendar")
        assert isinstance(result, dict)
        assert "GROUP PRIVACY MODE" in result["context"]
    finally:
        clear_session_vars(tokens)

    tokens = _dm_env()
    try:
        assert hooks["pre_llm_call"](user_message="what's on my calendar") is None
    finally:
        clear_session_vars(tokens)


def test_transform_scrubs_group_reply_but_leaves_dm_alone(hooks):
    secret = "Board meeting confirmed for owner@secretcorp.com at 3pm"

    tokens = _group_env()
    try:
        out = hooks["transform_llm_output"](response_text=secret)
        assert isinstance(out, str)
        assert "owner@secretcorp.com" not in out
        assert "[REDACTED]" in out
    finally:
        clear_session_vars(tokens)

    tokens = _dm_env()
    try:
        assert hooks["transform_llm_output"](response_text=secret) is None
    finally:
        clear_session_vars(tokens)


def test_transform_clean_group_text_passes_through_unchanged(hooks):
    tokens = _group_env()
    try:
        assert hooks["transform_llm_output"](response_text="Done!") is None
    finally:
        clear_session_vars(tokens)
