"""Unit tests for the omniwa-assist Hermes plugin.

Run inside the hermes container:
  /opt/hermes/.venv/bin/python -m pytest /opt/data/plugins/omniwa-assist/test_plugin.py -q

Covers the pre_gateway_dispatch gate:
  - OWNER self-chat DM passes through untouched (always-on command channel)
  - non-owner allowlisted DMs get the same gate as groups (ingest + keywords;
    mentions pass; noise skipped)
  - @mentions pass through untouched (existing direct-command UX)
  - keyword hits are rewritten into SUGGESTION MODE (offer, don't execute)
  - noise is skipped (zero API cost)
  - every WhatsApp sender is pushed to the contacts ingest endpoint
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
            chat_id=("120363406613211534@g.us" if chat_type == "group" else phone),
            chat_type=chat_type,
            user_id=f"{phone}",
            user_name="Tester",
        ),
        raw_message=raw,
    )


@pytest.fixture
def hooks(monkeypatch):
    monkeypatch.setenv("WHATSAPP_ASSIST_KEYWORDS", "book,remind,weather")
    monkeypatch.setenv("OWNER_WA_PHONE", "916300354385,916281192096")
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

def test_owner_selfchat_dm_always_passes(hooks, ingests):
    ev = _event(chat_type="dm", text="anything at all", phone="916300354385")
    assert hooks["pre_gateway_dispatch"](event=ev) is None


def test_nonowner_dm_is_gated_like_groups(hooks, ingests):
    # noise -> skip
    ev = _event(chat_type="dm", text="lol ok cool", phone="919999999999")
    assert hooks["pre_gateway_dispatch"](event=ev) == {"action": "skip", "reason": "assist_no_keyword"}
    # keyword -> suggestion rewrite
    ev2 = _event(chat_type="dm", text="can you book a table", phone="919999999999")
    result = hooks["pre_gateway_dispatch"](event=ev2)
    assert result["action"] == "rewrite"


def test_lid_form_selfchat_dm_passes(hooks, ingests):
    """Regression: self-chat arrives as LID (13349261734098@lid), which can
    never digit-match an OWNER_WA_PHONE. The chat-id-vs-botIds proof must
    catch it or the owner's own command channel gets skipped as noise."""
    ev = _event(chat_type="dm", text="ping", phone="916300354385")
    ev.source.chat_id = "13349261734098@lid"
    ev.raw_message["botIds"] = ["13349261734098@lid"]
    assert hooks["pre_gateway_dispatch"](event=ev) is None


def test_dm_ingested_for_directory(hooks, ingests):
    hooks["pre_gateway_dispatch"](event=_event(chat_type="dm", text="hi there"))
    assert ingests == [("919999999999", "919999999999", "Tester")]


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
