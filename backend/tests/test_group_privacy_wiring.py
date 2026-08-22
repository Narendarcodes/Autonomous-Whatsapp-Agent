"""Tests for Unit 2 — group privacy wiring in agent_harness.dispatch_to_hermes."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.services import agent_harness
from app.core.config import settings
from app.services.group_privacy_service import GROUP_PRIVACY_DIRECTIVE


GROUP_JID = "120363021212099999@g.us"
DM_PHONE = "919876543210"


def _fake_hermes_response(content: str) -> MagicMock:
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json.return_value = {
        "choices": [{"message": {"content": content}}]
    }
    return resp


@pytest.fixture
def mock_owner_prefs(monkeypatch):
    """Stub DB lookups so dispatch doesn't need a live DB."""
    def fake_session():
        class FakeResult:
            def scalar_one_or_none(self):
                return None

        class FakeDB:
            def execute(self, *_a, **_k):
                return FakeResult()

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

        return FakeDB()
    monkeypatch.setattr(agent_harness, "AsyncSessionLocal", fake_session)


@pytest.fixture
def legacy_delivery(monkeypatch):
    """Pin HERMES_OWNS_WHATSAPP=false so replies go through whatsapp_service.send_text.

    In production the Hermes Baileys bridge delivers replies itself; these
    tests exercise the backend's own send path (redaction before send).
    """
    monkeypatch.setattr(settings, "HERMES_OWNS_WHATSAPP", False, raising=False)


@pytest.mark.asyncio
async def test_group_session_gets_privacy_directive(mock_owner_prefs):
    captured = {}

    async def fake_post(url, json=None, headers=None, timeout=None):
        captured["system"] = json["messages"][0]["content"]
        return _fake_hermes_response("ok")

    with patch.object(agent_harness.httpx, "AsyncClient") as ac:
        ac.return_value.__aenter__ = AsyncMock(return_value=MagicMock(post=fake_post))
        ac.return_value.__aexit__ = AsyncMock(return_value=False)
        await agent_harness.dispatch_to_hermes(GROUP_JID, "hello bot")

    assert "GROUP PRIVACY MODE" in captured["system"]


@pytest.mark.asyncio
async def test_dm_session_no_privacy_directive(mock_owner_prefs):
    captured = {}

    async def fake_post(url, json=None, headers=None, timeout=None):
        captured["system"] = json["messages"][0]["content"]
        return _fake_hermes_response("ok")

    with patch.object(agent_harness.httpx, "AsyncClient") as ac:
        ac.return_value.__aenter__ = AsyncMock(return_value=MagicMock(post=fake_post))
        ac.return_value.__aexit__ = AsyncMock(return_value=False)
        await agent_harness.dispatch_to_hermes(DM_PHONE, "hello bot")

    assert "GROUP PRIVACY MODE" not in captured["system"]


@pytest.mark.asyncio
async def test_group_reply_is_redacted_before_send(mock_owner_prefs, legacy_delivery):
    sent = {}

    async def fake_post(url, json=None, headers=None, timeout=None):
        return _fake_hermes_response("Sure — event 'Board Meeting' at narendar@omniwa.app")

    async def _capture(chat_id, text):
        sent["chat_id"] = chat_id
        sent["text"] = text

    with patch.object(agent_harness.httpx, "AsyncClient") as ac, \
         patch("app.services.whatsapp_service.whatsapp_service.send_text", new=_capture):
        ac.return_value.__aenter__ = AsyncMock(return_value=MagicMock(post=fake_post))
        ac.return_value.__aexit__ = AsyncMock(return_value=False)

        await agent_harness.dispatch_to_hermes(GROUP_JID, "what's on my calendar")

    assert sent.get("chat_id") == GROUP_JID
    assert "narendar@omniwa.app" not in sent.get("text", "")
    assert "[REDACTED]" in sent.get("text", "")


@pytest.mark.asyncio
async def test_dm_reply_not_redacted(mock_owner_prefs, legacy_delivery):
    sent = {}

    async def fake_post(url, json=None, headers=None, timeout=None):
        return _fake_hermes_response("Your email is narendar@omniwa.app")

    async def _capture(chat_id, text):
        sent["text"] = text

    with patch.object(agent_harness.httpx, "AsyncClient") as ac, \
         patch("app.services.whatsapp_service.whatsapp_service.send_text", new=_capture):
        ac.return_value.__aenter__ = AsyncMock(return_value=MagicMock(post=fake_post))
        ac.return_value.__aexit__ = AsyncMock(return_value=False)

        await agent_harness.dispatch_to_hermes(DM_PHONE, "what's my email")

    assert "narendar@omniwa.app" in sent.get("text", "")
