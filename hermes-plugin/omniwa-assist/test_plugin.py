"""Unit tests for the omniwa-assist Hermes plugin.

Run inside the hermes container:
  /opt/hermes/.venv/bin/python -m pytest /opt/data/plugins/omniwa-assist/test_plugin.py -q

Covers the pre_gateway_dispatch gate:
  - DMs pass through untouched (None)
  - @mentions pass through untouched (existing direct-command UX)
  - keyword hits are rewritten into SUGGESTION MODE (offer, don't execute)
  - noise is skipped (zero API cost)
  - every group sender is pushed to the contacts ingest endpoint exactly once
"""
import importlib
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

PLUGIN_DIR = Path(__file__).resolve().parent
if str(PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(PLUGIN_DIR))

sys.modules.pop("omniwa_assist_testee", None)
spec = importlib.util.spec_from_file_location("omniwa_assist_testee", PLUGIN_DIR / "__init__.py")
plugin = importlib.util.module_from_spec(spec)
spec.loader.exec_module(plugin)


class FakeCtx:
    def __init__(self):
        self.hooks = {}

    def register_hook(self, name, fn):
        self.hooks[name] = fn


def _event(chat_type="group", text="hello", mentioned=None, bot_id="bot@lid", phone="919999999999"):
    raw = {"mentionedIds": mentioned or [], "botIds": [bot_id], "senderId": f"{phone}@lid"}
    return SimpleNamespace(
        text=text,
        source=SimpleNamespace(
            platform=SimpleNamespace(value="whatsapp"),
            chat_id=("120363406613211534@g.us" if chat_type == "group" else "919999999999"),
            chat_type=chat_type,
            user_id=f"{phone}",
            user_name="Tester",
        ),
        raw_message=raw,
    )


@pytest.fixture
def hooks(monkeypatch):
    monkeypatch.setenv("WHATSAPP_ASSIST_KEYWORDS", "book,remind,weather")
    ctx = FakeCtx()
    plugin.register(ctx)
    assert set(ctx.hooks) == {"pre_gateway_dispatch"}
    return ctx.hooks


@pytest.fixture
def ingests(monkeypatch):
    calls = []
    monkeypatch.setattr(plugin, "_ingest_configured", lambda: True)
    monkeypatch.setattr(plugin, "_spawn_ingest", lambda source, event: calls.append((source.chat_id, source.user_id, source.user_name)))
    return calls


# --- passthrough ---

def test_dm_untouched(hooks, ingests):
    assert hooks["pre_gateway_dispatch"](event=_event(chat_type="dm")) is None
    assert ingests == []  # DM ingestion handled elsewhere for now


def test_mention_passes_through_even_without_keyword(hooks, ingests):
    ev = _event(text="random chatter", mentioned=["bot@lid"])
    assert hooks["pre_gateway_dispatch"](event=ev) is None


# --- keyword gate ---

def test_keyword_hit_rewrites_into_suggestion_mode(hooks, ingests):
    ev = _event(text="someone should book a table for Friday")
    result = hooks["pre_gateway_dispatch"](event=ev)
    assert result["action"] == "rewrite"
    assert "[ASSIST" in result["text"]
    assert "someone should book a table for Friday" in result["text"]


def test_noise_is_skipped(hooks, ingests):
    ev = _event(text="lol ok cool")
    result = hooks["pre_gateway_dispatch"](event=ev)
    assert result == {"action": "skip", "reason": "assist_no_keyword"}


# --- ingest ---

def test_group_sender_ingested_once_per_message(hooks, ingests):
    ev = _event(text="plain message")
    hooks["pre_gateway_dispatch"](event=ev)
    assert ingests == [("120363406613211534@g.us", "919999999999", "Tester")]


def test_ingest_defaults_off_until_configured(monkeypatch, hooks):
    calls = []
    monkeypatch.setattr(plugin, "_spawn_ingest", lambda s, e: calls.append(1))
    monkeypatch.setattr(plugin, "_ingest_configured", lambda: False)
    hooks["pre_gateway_dispatch"](event=_event())
    assert calls == []


# --- regressions fixed 2026-11 ---

@pytest.fixture(autouse=True)
def _clear_offers():
    plugin._open_offers.clear()
    yield
    plugin._open_offers.clear()


@pytest.mark.parametrize("text", [
    "check my facebook", "new notebook", "near the border", "good explanation", "planet earth",
])
def test_keyword_substrings_do_not_trigger(hooks, ingests, text):
    assert hooks["pre_gateway_dispatch"](event=_event(text=text)) == {
        "action": "skip", "reason": "assist_no_keyword"}


def test_offer_can_be_accepted_with_plain_yes(hooks, ingests):
    """Bot offers help -> the same member's 'yes' must reach the agent."""
    hook = hooks["pre_gateway_dispatch"]
    assert hook(event=_event(text="can you book a table"))["action"] == "rewrite"
    assert hook(event=_event(text="yes please")) is None          # passes through
    # one-shot: the next unrelated chatter is gated again
    assert hook(event=_event(text="lol"))["action"] == "skip"


def test_offer_followup_only_for_same_sender(hooks, ingests):
    hook = hooks["pre_gateway_dispatch"]
    hook(event=_event(text="book a table", phone="911111111111"))
    assert hook(event=_event(text="yes", phone="912222222222"))["action"] == "skip"


def test_device_qualified_bot_mention_passes(hooks, ingests):
    ev = _event(text="hey", mentioned=["bot:12@lid"])
    assert hooks["pre_gateway_dispatch"](event=ev) is None


def test_slash_command_passes(hooks, ingests):
    assert hooks["pre_gateway_dispatch"](event=_event(text="/status")) is None


def test_sender_identity_strips_jid_suffix():
    src = SimpleNamespace(user_id="919999999999:3@s.whatsapp.net")
    assert plugin._sender_identity(src) == {"phone": "919999999999", "lid": None}
    src = SimpleNamespace(user_id="200283032441063@lid")
    assert plugin._sender_identity(src) == {"phone": "200283032441063@lid", "lid": "200283032441063@lid"}


# --- omniwa-guard deferral (plugin load order must not matter) ---

def test_guard_skip_beats_keyword_rewrite(hooks, ingests, monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("WHATSAPP_MODE", "bot")
    monkeypatch.setenv("OWNER_WA_PHONE", "919000000001")
    monkeypatch.delenv("OMNIWA_GROUP_GUESTS", raising=False)
    (tmp_path / "config.yaml").write_text("whatsapp:\n  allow_from: []\n", encoding="utf-8")
    out = hooks["pre_gateway_dispatch"](event=_event(text="can you book a table", phone="919888877777"))
    assert out == {"action": "skip", "reason": "omniwa_guard_group_member_not_allowlisted"}
    assert len(ingests) == 1  # still recorded in the contacts directory
