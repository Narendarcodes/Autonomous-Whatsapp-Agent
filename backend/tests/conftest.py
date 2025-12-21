"""
Pytest Configuration and Fixtures
"""

import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from typing import Generator, AsyncGenerator
import json

# Configure pytest-asyncio
pytest_plugins = ('pytest_asyncio',)


@pytest.fixture(scope="session")
def event_loop():
    """Create an instance of the event loop for the test session."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def mock_settings():
    """Mock settings for testing"""
    with patch('app.core.config.settings') as mock:
        mock.DEBUG = True
        mock.ENVIRONMENT = "testing"
        mock.APP_NAME = "Test App"
        mock.APP_VERSION = "1.0.0"
        mock.WHATSAPP_TOKEN = "test_token"
        mock.WHATSAPP_PHONE_ID = "test_phone_id"
        mock.WHATSAPP_VERIFY_TOKEN = "test_verify_token"
        mock.WHATSAPP_SEND_MESSAGE_URL = "https://graph.facebook.com/v21.0/test_phone_id/messages"
        mock.WHATSAPP_APP_SECRET = "test_secret"
        mock.REDIS_URL = "redis://localhost:6379/0"
        mock.GOOGLE_CLIENT_ID = "test_client_id"
        mock.GOOGLE_CLIENT_SECRET = "test_client_secret"
        mock.GOOGLE_REDIRECT_URI = "http://localhost:8000/oauth/callback"
        mock.SESSION_TTL = 3600
        mock.CONVERSATION_MAX_MESSAGES = 10
        mock.CONVERSATION_TTL = 3600
        mock.RATE_LIMIT_REQUESTS = 10
        mock.RATE_LIMIT_WINDOW = 60
        mock.HTTP_CONNECT_TIMEOUT = 10.0
        mock.HTTP_READ_TIMEOUT = 30.0
        mock.HTTP_WRITE_TIMEOUT = 30.0
        mock.HTTP_POOL_TIMEOUT = 10.0
        mock.CIRCUIT_FAILURE_THRESHOLD = 5
        mock.CIRCUIT_SUCCESS_THRESHOLD = 2
        mock.CIRCUIT_TIMEOUT_SECONDS = 60
        mock.RETRY_MAX_ATTEMPTS = 3
        mock.RETRY_BASE_DELAY = 1.0
        mock.RETRY_MAX_DELAY = 10.0
        mock.REDIS_CONNECT_TIMEOUT = 5.0
        mock.REDIS_SOCKET_TIMEOUT = 5.0
        yield mock


@pytest.fixture
def mock_redis_client():
    """Mock Redis client for testing"""
    mock = AsyncMock()
    mock.client = AsyncMock()
    mock._ensure_connected = AsyncMock(return_value=True)
    mock.cache_get = AsyncMock(return_value=None)
    mock.cache_set = AsyncMock(return_value=True)
    mock.check_rate_limit = AsyncMock(return_value=True)
    mock.get_session = AsyncMock(return_value=None)
    mock.set_session = AsyncMock(return_value=True)
    mock.add_message = AsyncMock(return_value=True)
    mock.get_conversation = AsyncMock(return_value=[])
    return mock


@pytest.fixture
def mock_httpx_client():
    """Mock httpx client for testing"""
    mock = AsyncMock()
    mock.is_closed = False
    return mock


@pytest.fixture
def sample_whatsapp_webhook_payload():
    """Sample WhatsApp webhook payload"""
    return {
        "object": "whatsapp_business_account",
        "entry": [{
            "id": "123456789",
            "changes": [{
                "value": {
                    "messaging_product": "whatsapp",
                    "metadata": {
                        "display_phone_number": "15551234567",
                        "phone_number_id": "test_phone_id"
                    },
                    "contacts": [{
                        "profile": {"name": "Test User"},
                        "wa_id": "15559876543"
                    }],
                    "messages": [{
                        "from": "15559876543",
                        "id": "wamid.test123",
                        "timestamp": "1638316800",
                        "text": {"body": "What's on my calendar today?"},
                        "type": "text"
                    }]
                },
                "field": "messages"
            }]
        }]
    }


@pytest.fixture
def sample_calendar_event():
    """Sample Google Calendar event"""
    return {
        "id": "event_123",
        "summary": "Team Meeting",
        "description": "Weekly sync",
        "start": {
            "dateTime": "2025-12-01T10:00:00Z",
            "timeZone": "UTC"
        },
        "end": {
            "dateTime": "2025-12-01T11:00:00Z",
            "timeZone": "UTC"
        },
        "location": "Conference Room A",
        "status": "confirmed"
    }


@pytest.fixture
def sample_user_data():
    """Sample user data"""
    return {
        "id": "550e8400-e29b-41d4-a716-446655440000",
        "wa_phone": "+15559876543",
        "name": "Test User",
        "is_authorized": True,
        "google_access_token": "test_access_token",
        "google_refresh_token": "test_refresh_token"
    }
