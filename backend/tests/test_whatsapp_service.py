"""
Tests for WhatsApp Service
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import httpx


class TestWhatsAppServiceInit:
    """Test cases for WhatsAppService initialization"""
    
    def test_service_initialization(self):
        """Should initialize with correct settings"""
        with patch('app.services.whatsapp_service.settings') as mock_settings:
            mock_settings.WHATSAPP_SEND_MESSAGE_URL = "https://test.api/messages"
            mock_settings.WHATSAPP_TOKEN = "test_token"
            mock_settings.WHATSAPP_PHONE_ID = "123456"
            mock_settings.CIRCUIT_FAILURE_THRESHOLD = 5
            mock_settings.CIRCUIT_SUCCESS_THRESHOLD = 2
            mock_settings.CIRCUIT_TIMEOUT_SECONDS = 60
            mock_settings.HTTP_CONNECT_TIMEOUT = 10.0
            mock_settings.HTTP_READ_TIMEOUT = 30.0
            mock_settings.HTTP_WRITE_TIMEOUT = 30.0
            mock_settings.HTTP_POOL_TIMEOUT = 10.0
            
            from app.services.whatsapp_service import WhatsAppService
            service = WhatsAppService()
            
            assert service.api_url == "https://test.api/messages"
            assert service.access_token == "test_token"
            assert service._circuit_breaker is not None


class TestWhatsAppServiceSendMessage:
    """Test cases for send_text_message"""
    
    @pytest.mark.asyncio
    async def test_send_text_message_success(self):
        """Should send message successfully"""
        with patch('app.services.whatsapp_service.settings') as mock_settings:
            mock_settings.WHATSAPP_SEND_MESSAGE_URL = "https://test.api/messages"
            mock_settings.WHATSAPP_TOKEN = "test_token"
            mock_settings.WHATSAPP_PHONE_ID = "123456"
            mock_settings.CIRCUIT_FAILURE_THRESHOLD = 5
            mock_settings.CIRCUIT_SUCCESS_THRESHOLD = 2
            mock_settings.CIRCUIT_TIMEOUT_SECONDS = 60
            mock_settings.HTTP_CONNECT_TIMEOUT = 10.0
            mock_settings.HTTP_READ_TIMEOUT = 30.0
            mock_settings.HTTP_WRITE_TIMEOUT = 30.0
            mock_settings.HTTP_POOL_TIMEOUT = 10.0
            mock_settings.RETRY_MAX_ATTEMPTS = 3
            mock_settings.RETRY_BASE_DELAY = 0.01
            mock_settings.RETRY_MAX_DELAY = 0.1
            
            from app.services.whatsapp_service import WhatsAppService
            service = WhatsAppService()
            
            # Mock the HTTP client
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {
                "messages": [{"id": "wamid.test123"}]
            }
            
            mock_client = AsyncMock()
            mock_client.is_closed = False
            mock_client.post = AsyncMock(return_value=mock_response)
            service._client = mock_client
            
            result = await service.send_text_message(
                to="+15551234567",
                message="Hello, test!"
            )
            
            assert result is True
            mock_client.post.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_send_text_message_api_error(self):
        """Should return False on API error"""
        with patch('app.services.whatsapp_service.settings') as mock_settings:
            mock_settings.WHATSAPP_SEND_MESSAGE_URL = "https://test.api/messages"
            mock_settings.WHATSAPP_TOKEN = "test_token"
            mock_settings.WHATSAPP_PHONE_ID = "123456"
            mock_settings.CIRCUIT_FAILURE_THRESHOLD = 5
            mock_settings.CIRCUIT_SUCCESS_THRESHOLD = 2
            mock_settings.CIRCUIT_TIMEOUT_SECONDS = 60
            mock_settings.HTTP_CONNECT_TIMEOUT = 10.0
            mock_settings.HTTP_READ_TIMEOUT = 30.0
            mock_settings.HTTP_WRITE_TIMEOUT = 30.0
            mock_settings.HTTP_POOL_TIMEOUT = 10.0
            mock_settings.RETRY_MAX_ATTEMPTS = 3
            mock_settings.RETRY_BASE_DELAY = 0.01
            mock_settings.RETRY_MAX_DELAY = 0.1
            
            from app.services.whatsapp_service import WhatsAppService
            service = WhatsAppService()
            
            mock_response = MagicMock()
            mock_response.status_code = 400
            mock_response.text = "Bad Request"
            
            mock_client = AsyncMock()
            mock_client.is_closed = False
            mock_client.post = AsyncMock(return_value=mock_response)
            service._client = mock_client
            
            result = await service.send_text_message(
                to="+15551234567",
                message="Hello!"
            )
            
            assert result is False
    
    @pytest.mark.asyncio
    async def test_send_text_message_circuit_open(self):
        """Should return False when circuit breaker is open"""
        with patch('app.services.whatsapp_service.settings') as mock_settings:
            mock_settings.WHATSAPP_SEND_MESSAGE_URL = "https://test.api/messages"
            mock_settings.WHATSAPP_TOKEN = "test_token"
            mock_settings.WHATSAPP_PHONE_ID = "123456"
            mock_settings.CIRCUIT_FAILURE_THRESHOLD = 2
            mock_settings.CIRCUIT_SUCCESS_THRESHOLD = 2
            mock_settings.CIRCUIT_TIMEOUT_SECONDS = 60
            mock_settings.HTTP_CONNECT_TIMEOUT = 10.0
            mock_settings.HTTP_READ_TIMEOUT = 30.0
            mock_settings.HTTP_WRITE_TIMEOUT = 30.0
            mock_settings.HTTP_POOL_TIMEOUT = 10.0
            mock_settings.RETRY_MAX_ATTEMPTS = 1
            mock_settings.RETRY_BASE_DELAY = 0.01
            mock_settings.RETRY_MAX_DELAY = 0.1
            
            from app.services.whatsapp_service import WhatsAppService
            from app.core.circuit_breaker import CircuitState
            
            service = WhatsAppService()
            
            # Force circuit to OPEN state
            service._circuit_breaker.state = CircuitState.OPEN
            service._circuit_breaker.failure_count = 5
            
            result = await service.send_text_message(
                to="+15551234567",
                message="Hello!"
            )
            
            assert result is False


class TestWhatsAppServiceButtonMessage:
    """Test cases for send_button_message"""
    
    @pytest.mark.asyncio
    async def test_send_button_message_success(self):
        """Should send button message successfully"""
        with patch('app.services.whatsapp_service.settings') as mock_settings:
            mock_settings.WHATSAPP_SEND_MESSAGE_URL = "https://test.api/messages"
            mock_settings.WHATSAPP_TOKEN = "test_token"
            mock_settings.WHATSAPP_PHONE_ID = "123456"
            mock_settings.CIRCUIT_FAILURE_THRESHOLD = 5
            mock_settings.CIRCUIT_SUCCESS_THRESHOLD = 2
            mock_settings.CIRCUIT_TIMEOUT_SECONDS = 60
            mock_settings.HTTP_CONNECT_TIMEOUT = 10.0
            mock_settings.HTTP_READ_TIMEOUT = 30.0
            mock_settings.HTTP_WRITE_TIMEOUT = 30.0
            mock_settings.HTTP_POOL_TIMEOUT = 10.0
            mock_settings.RETRY_MAX_ATTEMPTS = 3
            mock_settings.RETRY_BASE_DELAY = 0.01
            mock_settings.RETRY_MAX_DELAY = 0.1
            
            from app.services.whatsapp_service import WhatsAppService
            service = WhatsAppService()
            
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {
                "messages": [{"id": "wamid.test123"}]
            }
            
            mock_client = AsyncMock()
            mock_client.is_closed = False
            mock_client.post = AsyncMock(return_value=mock_response)
            service._client = mock_client
            
            buttons = [
                {"id": "btn_yes", "title": "Yes"},
                {"id": "btn_no", "title": "No"}
            ]
            
            result = await service.send_button_message(
                to="+15551234567",
                text="Choose an option:",
                buttons=buttons
            )
            
            assert result is True
    
    @pytest.mark.asyncio
    async def test_button_title_truncation(self):
        """Should truncate button titles to 20 characters"""
        with patch('app.services.whatsapp_service.settings') as mock_settings:
            mock_settings.WHATSAPP_SEND_MESSAGE_URL = "https://test.api/messages"
            mock_settings.WHATSAPP_TOKEN = "test_token"
            mock_settings.WHATSAPP_PHONE_ID = "123456"
            mock_settings.CIRCUIT_FAILURE_THRESHOLD = 5
            mock_settings.CIRCUIT_SUCCESS_THRESHOLD = 2
            mock_settings.CIRCUIT_TIMEOUT_SECONDS = 60
            mock_settings.HTTP_CONNECT_TIMEOUT = 10.0
            mock_settings.HTTP_READ_TIMEOUT = 30.0
            mock_settings.HTTP_WRITE_TIMEOUT = 30.0
            mock_settings.HTTP_POOL_TIMEOUT = 10.0
            mock_settings.RETRY_MAX_ATTEMPTS = 3
            mock_settings.RETRY_BASE_DELAY = 0.01
            mock_settings.RETRY_MAX_DELAY = 0.1
            
            from app.services.whatsapp_service import WhatsAppService
            service = WhatsAppService()
            
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {"messages": [{"id": "test"}]}
            
            mock_client = AsyncMock()
            mock_client.is_closed = False
            mock_client.post = AsyncMock(return_value=mock_response)
            service._client = mock_client
            
            buttons = [
                {"id": "btn", "title": "This is a very long button title that exceeds limit"}
            ]
            
            await service.send_button_message(
                to="+15551234567",
                text="Test",
                buttons=buttons
            )
            
            # Verify the payload was truncated
            call_args = mock_client.post.call_args
            payload = call_args.kwargs['json']
            btn_title = payload['interactive']['action']['buttons'][0]['reply']['title']
            assert len(btn_title) <= 20


class TestWhatsAppServiceCircuitStatus:
    """Test cases for circuit breaker status"""
    
    def test_get_circuit_status(self):
        """Should return circuit breaker status"""
        with patch('app.services.whatsapp_service.settings') as mock_settings:
            mock_settings.WHATSAPP_SEND_MESSAGE_URL = "https://test.api/messages"
            mock_settings.WHATSAPP_TOKEN = "test_token"
            mock_settings.WHATSAPP_PHONE_ID = "123456"
            mock_settings.CIRCUIT_FAILURE_THRESHOLD = 5
            mock_settings.CIRCUIT_SUCCESS_THRESHOLD = 2
            mock_settings.CIRCUIT_TIMEOUT_SECONDS = 60
            mock_settings.HTTP_CONNECT_TIMEOUT = 10.0
            mock_settings.HTTP_READ_TIMEOUT = 30.0
            mock_settings.HTTP_WRITE_TIMEOUT = 30.0
            mock_settings.HTTP_POOL_TIMEOUT = 10.0
            
            from app.services.whatsapp_service import WhatsAppService
            service = WhatsAppService()
            
            status = service.get_circuit_status()
            
            assert "state" in status
            assert "failure_count" in status
            assert "success_count" in status
            assert status["state"] == "closed"


class TestWhatsAppServiceClose:
    """Test cases for client cleanup"""
    
    @pytest.mark.asyncio
    async def test_close_client(self):
        """Should close HTTP client properly"""
        with patch('app.services.whatsapp_service.settings') as mock_settings:
            mock_settings.WHATSAPP_SEND_MESSAGE_URL = "https://test.api/messages"
            mock_settings.WHATSAPP_TOKEN = "test_token"
            mock_settings.WHATSAPP_PHONE_ID = "123456"
            mock_settings.CIRCUIT_FAILURE_THRESHOLD = 5
            mock_settings.CIRCUIT_SUCCESS_THRESHOLD = 2
            mock_settings.CIRCUIT_TIMEOUT_SECONDS = 60
            mock_settings.HTTP_CONNECT_TIMEOUT = 10.0
            mock_settings.HTTP_READ_TIMEOUT = 30.0
            mock_settings.HTTP_WRITE_TIMEOUT = 30.0
            mock_settings.HTTP_POOL_TIMEOUT = 10.0
            
            from app.services.whatsapp_service import WhatsAppService
            service = WhatsAppService()
            
            mock_client = AsyncMock()
            mock_client.is_closed = False
            mock_client.aclose = AsyncMock()
            service._client = mock_client
            
            await service.close()
            
            mock_client.aclose.assert_called_once()
            assert service._client is None
