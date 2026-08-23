"""Tests for the Hermes Baileys bridge HTTP client (v3 outbound transport).

The bridge exposes POST /send {chatId, message} and GET /health on the
Hermes gateway port. These tests mock httpx at the module boundary and
verify URL/payload shapes, retry behaviour and status mapping.
"""
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from app.core.config import settings
from app.services import bridge_client


@pytest.fixture(autouse=True)
def reset_client():
    """Ensure the singleton httpx client is cleared around each test."""
    bridge_client._client = None
    yield
    bridge_client._client = None


def _mock_client():
    client = AsyncMock(spec=httpx.AsyncClient)
    return client


@pytest.mark.asyncio
async def test_send_text_success(mocker):
    """POST /send with chatId+message; returns True on 200."""
    client = _mock_client()
    mocker.patch.object(bridge_client, "_get_client", return_value=client)
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = 200
    client.post.return_value = resp

    ok = await bridge_client.send_text("919999999999", "hello")

    assert ok is True
    client.post.assert_called_once()
    url = client.post.call_args[0][0]
    payload = client.post.call_args[1]["json"]
    assert url == "/send"
    assert bridge_client._base_url() == settings.HERMES_BASE_URL.rstrip("/")
    assert payload == {"chatId": "919999999999", "message": "hello"}


@pytest.mark.asyncio
async def test_send_text_accepts_group_jid(mocker):
    """Group JIDs pass through untouched as chatId."""
    client = _mock_client()
    mocker.patch.object(bridge_client, "_get_client", return_value=client)
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = 200
    client.post.return_value = resp

    jid = "120312345678-987654321@g.us"
    ok = await bridge_client.send_text(jid, "hi group")

    assert ok is True
    assert client.post.call_args[1]["json"]["chatId"] == jid


@pytest.mark.asyncio
async def test_send_text_empty_target_short_circuits(mocker):
    """Empty chatId or message never hits the network."""
    client = _mock_client()
    mocker.patch.object(bridge_client, "_get_client", return_value=client)

    assert await bridge_client.send_text("", "msg") is False
    assert await bridge_client.send_text("919999999999", "  ") is False
    client.post.assert_not_called()


@pytest.mark.asyncio
async def test_send_text_retries_on_transport_error(mocker):
    """Transport errors back off and retry; False after max attempts."""
    client = _mock_client()
    mocker.patch.object(bridge_client, "_get_client", return_value=client)
    client.post.side_effect = httpx.HTTPError("bridge down")
    sleep_mock = mocker.patch("asyncio.sleep", new_callable=AsyncMock)

    ok = await bridge_client.send_text("919999999999", "hello")

    assert ok is False
    assert client.post.call_count == settings.RETRY_MAX_ATTEMPTS
    assert sleep_mock.call_count == settings.RETRY_MAX_ATTEMPTS


@pytest.mark.asyncio
async def test_send_text_503_when_disconnected(mocker):
    """Bridge returns 503 when WhatsApp is down — treated as failure, retried."""
    client = _mock_client()
    mocker.patch.object(bridge_client, "_get_client", return_value=client)
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = 503
    resp.text = '{"error": "Not connected to WhatsApp"}'
    client.post.return_value = resp
    mocker.patch("asyncio.sleep", new_callable=AsyncMock)

    ok = await bridge_client.send_text("919999999999", "hello")

    assert ok is False
    assert client.post.call_count == settings.RETRY_MAX_ATTEMPTS


@pytest.mark.asyncio
async def test_bridge_status_ok(mocker):
    """GET /health maps a 200 response to its connection state string."""
    client = _mock_client()
    mocker.patch.object(bridge_client, "_get_client", return_value=client)
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = 200
    resp.json.return_value = {"status": "connected"}
    client.get.return_value = resp

    status = await bridge_client.bridge_status()

    assert status == "connected"


@pytest.mark.asyncio
async def test_bridge_status_unreachable(mocker):
    """Connection errors map to an 'error: ...' sentinel."""
    client = _mock_client()
    mocker.patch.object(bridge_client, "_get_client", return_value=client)
    client.get.side_effect = httpx.HTTPError("refused")

    status = await bridge_client.bridge_status()

    assert status.startswith("error:")
