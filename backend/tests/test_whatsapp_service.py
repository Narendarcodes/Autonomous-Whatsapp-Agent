import pytest
from unittest.mock import AsyncMock, MagicMock
import httpx
from app.services.whatsapp_service import whatsapp_service
from app.core.config import settings

@pytest.fixture(autouse=True)
async def cleanup_client():
    """Ensure the singleton client is closed/cleared before and after each test."""
    await whatsapp_service.close()
    yield
    await whatsapp_service.close()


def test_to_chat_id():
    """Verify normalization of strings, raw phone numbers, and WhatsApp JIDs."""
    # Already formatted group JIDs should remain untouched
    assert whatsapp_service.to_chat_id("12345-67890@g.us") == "12345-67890@g.us"
    # Formatted user JIDs should remain untouched
    assert whatsapp_service.to_chat_id("919999999999@s.whatsapp.net") == "919999999999@s.whatsapp.net"
    # Raw phone numbers should be converted to JID format
    assert whatsapp_service.to_chat_id("+91 99999 99999") == "919999999999@s.whatsapp.net"
    # Check blank values
    assert whatsapp_service.to_chat_id("   ") == ""
    assert whatsapp_service.to_chat_id(None) == ""


@pytest.mark.asyncio
async def test_send_text_success(mocker):
    """Test successful message delivery, including parsing and caching message IDs."""
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    whatsapp_service._client = mock_client
    
    # Mock HTTP response
    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"key": {"id": "MSG_123456"}}
    mock_client.post.return_value = mock_resp

    # Mock Redis client cache_set to assert caching of sent message ID
    mock_cache = mocker.patch("app.services.whatsapp_service.cache_set", new_callable=AsyncMock)

    success = await whatsapp_service.send_text("919999999999", "Hello Hermes!")
    assert success is True
    
    mock_client.post.assert_called_once()
    called_url = mock_client.post.call_args[0][0]
    called_json = mock_client.post.call_args[1]["json"]
    
    assert f"/message/sendText/{settings.OPENWA_SESSION_ID}" in called_url
    assert called_json["number"] == "919999999999@s.whatsapp.net"
    assert called_json["text"] == "Hello Hermes!"
    
    # Assert sent message JID/ID cached to avoid self-replies
    mock_cache.assert_any_call("sent_message:MSG_123456", "1", ttl_seconds=3600)


@pytest.mark.asyncio
async def test_send_text_retry_and_fail(mocker):
    """Verify retry policy and exponential backoff triggers on connection errors."""
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    whatsapp_service._client = mock_client
    
    # Inject connection failure
    mock_client.post.side_effect = httpx.HTTPError("API offline")

    # Mock sleep to avoid test execution delay
    mocker.patch("asyncio.sleep")

    success = await whatsapp_service.send_text("919999999999", "Retry test")
    assert success is False
    
    # Check retry loop executed RETRY_MAX_ATTEMPTS times
    assert mock_client.post.call_count == settings.RETRY_MAX_ATTEMPTS


@pytest.mark.asyncio
async def test_send_audio_fallback(mocker):
    """Verify audio sending automatically redirects to fallback endpoints on 404 errors."""
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    whatsapp_service._client = mock_client

    # Mock 404 response on the primary Baileys endpoint
    mock_resp_404 = MagicMock(spec=httpx.Response)
    mock_resp_404.status_code = 404
    mock_resp_404.text = "Endpoint not supported"
    
    # Mock success on the fallback endpoint
    mock_resp_200 = MagicMock(spec=httpx.Response)
    mock_resp_200.status_code = 200
    mock_resp_200.json.return_value = {"key": {"id": "AUDIO_123"}}

    mock_client.post.side_effect = [mock_resp_404, mock_resp_200]

    success = await whatsapp_service.send_audio("919999999999", "YXVkaW8tYmFzZTY0") # base64 dummy
    assert success is True
    
    assert mock_client.post.call_count == 2
    
    # Primary URL endpoint
    url_primary = mock_client.post.call_args_list[0][0][0]
    assert "sendWhatsAppAudio" in url_primary

    # Fallback URL endpoint
    url_fallback = mock_client.post.call_args_list[1][0][0]
    assert "sendAudio" in url_fallback


@pytest.mark.asyncio
async def test_instance_management_flows(mocker):
    """Test WhatsApp instance registration, existing instance status (403), and instance removal."""
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    whatsapp_service._client = mock_client

    # 1. create_instance handles "already exists" (403) from API and falls back to configuring webhook
    mock_resp_403 = MagicMock(spec=httpx.Response)
    mock_resp_403.status_code = 403
    mock_resp_403.json.return_value = {"message": "Instance already exists"}
    
    mock_resp_webhook = MagicMock(spec=httpx.Response)
    mock_resp_webhook.status_code = 200
    
    mock_client.post.side_effect = [mock_resp_403, mock_resp_webhook]

    res = await whatsapp_service.create_instance()
    assert res is True
    assert mock_client.post.call_count == 2

    # 2. delete_instance successfully updates connection status cache
    mock_resp_del = MagicMock(spec=httpx.Response)
    mock_resp_del.status_code = 200
    mock_client.delete.return_value = mock_resp_del
    
    mock_cache = mocker.patch("app.db.redis_client.cache_set", new_callable=AsyncMock)

    del_res = await whatsapp_service.delete_instance()
    assert del_res is True
    mock_client.delete.assert_called_once()
    assert mock_cache.call_count == 2
