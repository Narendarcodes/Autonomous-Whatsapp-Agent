"""
Tests for Webhook Endpoints
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient
import json
import hmac
import hashlib


class TestWebhookVerification:
    """Test cases for webhook verification endpoint"""
    
    def test_verify_webhook_success(self):
        """Should verify webhook with correct token"""
        with patch('app.api.webhooks.settings') as mock_settings:
            mock_settings.WHATSAPP_VERIFY_TOKEN = "test_verify_token"
            mock_settings.DEBUG = True
            mock_settings.ENVIRONMENT = "testing"
            
            with patch('app.api.webhooks.redis_client'):
                from app.api.webhooks import router
                from fastapi import FastAPI
                
                app = FastAPI()
                app.include_router(router)
                client = TestClient(app)
                
                response = client.get(
                    "/webhook",
                    params={
                        "hub.mode": "subscribe",
                        "hub.challenge": "challenge123",
                        "hub.verify_token": "test_verify_token"
                    }
                )
                
                assert response.status_code == 200
                assert response.text == "challenge123"
    
    def test_verify_webhook_invalid_token(self):
        """Should reject webhook with invalid token"""
        with patch('app.api.webhooks.settings') as mock_settings:
            mock_settings.WHATSAPP_VERIFY_TOKEN = "test_verify_token"
            mock_settings.DEBUG = True
            
            with patch('app.api.webhooks.redis_client'):
                from app.api.webhooks import router
                from fastapi import FastAPI
                
                app = FastAPI()
                app.include_router(router)
                client = TestClient(app)
                
                response = client.get(
                    "/webhook",
                    params={
                        "hub.mode": "subscribe",
                        "hub.challenge": "challenge123",
                        "hub.verify_token": "wrong_token"
                    }
                )
                
                assert response.status_code == 403
    
    def test_verify_webhook_invalid_mode(self):
        """Should reject webhook with invalid mode"""
        with patch('app.api.webhooks.settings') as mock_settings:
            mock_settings.WHATSAPP_VERIFY_TOKEN = "test_verify_token"
            mock_settings.DEBUG = True
            
            with patch('app.api.webhooks.redis_client'):
                from app.api.webhooks import router
                from fastapi import FastAPI
                
                app = FastAPI()
                app.include_router(router)
                client = TestClient(app)
                
                response = client.get(
                    "/webhook",
                    params={
                        "hub.mode": "unsubscribe",  # Wrong mode
                        "hub.challenge": "challenge123",
                        "hub.verify_token": "test_verify_token"
                    }
                )
                
                assert response.status_code == 403


class TestWebhookSignatureVerification:
    """Test cases for webhook signature verification"""
    
    def test_verify_signature_valid(self):
        """Should verify valid signature"""
        from app.api.webhooks import verify_webhook_signature
        
        payload = b'{"test": "data"}'
        secret = "test_secret"
        
        # Generate valid signature
        expected_sig = hmac.new(
            secret.encode('utf-8'),
            payload,
            hashlib.sha256
        ).hexdigest()
        
        result = verify_webhook_signature(
            payload,
            f"sha256={expected_sig}",
            secret
        )
        
        assert result is True
    
    def test_verify_signature_invalid(self):
        """Should reject invalid signature"""
        from app.api.webhooks import verify_webhook_signature
        
        payload = b'{"test": "data"}'
        secret = "test_secret"
        
        result = verify_webhook_signature(
            payload,
            "sha256=invalid_signature",
            secret
        )
        
        assert result is False
    
    def test_verify_signature_missing(self):
        """Should reject missing signature"""
        from app.api.webhooks import verify_webhook_signature
        
        result = verify_webhook_signature(
            b'{"test": "data"}',
            "",
            "test_secret"
        )
        
        assert result is False
    
    def test_verify_signature_no_secret(self):
        """Should reject when no secret configured"""
        from app.api.webhooks import verify_webhook_signature
        
        result = verify_webhook_signature(
            b'{"test": "data"}',
            "sha256=abc123",
            ""
        )
        
        assert result is False


class TestWebhookMessageProcessing:
    """Test cases for message processing"""
    
    @pytest.mark.asyncio
    async def test_process_text_message(self):
        """Should process text message and queue to stream"""
        with patch('app.api.webhooks.settings') as mock_settings, \
             patch('app.api.webhooks.redis_client') as mock_redis, \
             patch('app.api.webhooks.RedisStreamProducer') as mock_producer_class:
            
            mock_settings.DEBUG = True
            mock_settings.ENVIRONMENT = "testing"
            mock_settings.WHATSAPP_APP_SECRET = None
            
            mock_redis.cache_get = AsyncMock(return_value=None)
            mock_redis.cache_set = AsyncMock(return_value=True)
            mock_redis.check_rate_limit = AsyncMock(return_value=True)
            
            mock_producer = AsyncMock()
            mock_producer.push_message = AsyncMock(return_value="stream_id_123")
            mock_producer_class.return_value = mock_producer
            
            from app.api.webhooks import router
            from fastapi import FastAPI
            
            app = FastAPI()
            app.include_router(router)
            
            from httpx import AsyncClient, ASGITransport
            
            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test"
            ) as client:
                payload = {
                    "object": "whatsapp_business_account",
                    "entry": [{
                        "id": "123",
                        "changes": [{
                            "value": {
                                "messaging_product": "whatsapp",
                                "metadata": {
                                    "display_phone_number": "15551234567",
                                    "phone_number_id": "test_id"
                                },
                                "contacts": [{
                                    "profile": {"name": "Test User"},
                                    "wa_id": "15559876543"
                                }],
                                "messages": [{
                                    "from": "15559876543",
                                    "id": "wamid.test",
                                    "timestamp": "1638316800",
                                    "text": {"body": "Hello!"},
                                    "type": "text"
                                }]
                            },
                            "field": "messages"
                        }]
                    }]
                }
                
                response = await client.post(
                    "/webhook",
                    json=payload
                )
                
                assert response.status_code == 200


class TestMessageLengthValidation:
    """Test cases for message length validation"""
    
    def test_message_length_constants(self):
        """Should have correct length constants"""
        from app.api.webhooks import MAX_MESSAGE_LENGTH, MIN_MESSAGE_LENGTH
        
        assert MAX_MESSAGE_LENGTH == 4096
        assert MIN_MESSAGE_LENGTH == 1
    
    @pytest.mark.asyncio
    async def test_empty_message_skipped(self):
        """Should skip empty messages"""
        with patch('app.api.webhooks.settings') as mock_settings, \
             patch('app.api.webhooks.redis_client') as mock_redis, \
             patch('app.api.webhooks.RedisStreamProducer') as mock_producer_class:
            
            mock_settings.DEBUG = True
            mock_settings.ENVIRONMENT = "testing"
            mock_settings.WHATSAPP_APP_SECRET = None
            
            mock_producer = AsyncMock()
            mock_producer.push_message = AsyncMock(return_value="stream_id")
            mock_producer_class.return_value = mock_producer
            
            from app.api.webhooks import router
            from fastapi import FastAPI
            
            app = FastAPI()
            app.include_router(router)
            
            from httpx import AsyncClient, ASGITransport
            
            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test"
            ) as client:
                payload = {
                    "object": "whatsapp_business_account",
                    "entry": [{
                        "id": "123",
                        "changes": [{
                            "value": {
                                "messaging_product": "whatsapp",
                                "metadata": {
                                    "display_phone_number": "15551234567",
                                    "phone_number_id": "test_id"
                                },
                                "contacts": [{
                                    "profile": {"name": "Test User"},
                                    "wa_id": "15559876543"
                                }],
                                "messages": [{
                                    "from": "15559876543",
                                    "id": "wamid.test",
                                    "timestamp": "1638316800",
                                    "text": {"body": ""},  # Empty message
                                    "type": "text"
                                }]
                            },
                            "field": "messages"
                        }]
                    }]
                }
                
                response = await client.post("/webhook", json=payload)
                
                assert response.status_code == 200
                # Message should not be pushed due to empty content
                mock_producer.push_message.assert_not_called()


class TestTestWebhookEndpoint:
    """Test cases for test webhook endpoint"""
    
    def test_test_webhook_enabled_in_debug(self):
        """Should be accessible in debug mode"""
        with patch('app.api.webhooks.settings') as mock_settings, \
             patch('app.api.webhooks.redis_client'):
            
            mock_settings.DEBUG = True
            mock_settings.ENVIRONMENT = "testing"
            
            from app.api.webhooks import router
            from fastapi import FastAPI
            
            app = FastAPI()
            app.include_router(router)
            client = TestClient(app)
            
            response = client.post(
                "/webhook/test",
                json={"test": "data"}
            )
            
            assert response.status_code == 200
            assert response.json()["status"] == "test_ok"
    
    def test_test_webhook_disabled_in_production(self):
        """Should return 404 in production"""
        with patch('app.api.webhooks.settings') as mock_settings, \
             patch('app.api.webhooks.redis_client'):
            
            mock_settings.DEBUG = False
            mock_settings.ENVIRONMENT = "production"
            
            from app.api.webhooks import router
            from fastapi import FastAPI
            
            app = FastAPI()
            app.include_router(router)
            client = TestClient(app)
            
            response = client.post(
                "/webhook/test",
                json={"test": "data"}
            )
            
            assert response.status_code == 404
    
    def test_test_webhook_disabled_debug_production(self):
        """Should return 404 even with DEBUG=True in production environment"""
        with patch('app.api.webhooks.settings') as mock_settings, \
             patch('app.api.webhooks.redis_client'):
            
            mock_settings.DEBUG = True
            mock_settings.ENVIRONMENT = "production"  # Production environment
            
            from app.api.webhooks import router
            from fastapi import FastAPI
            
            app = FastAPI()
            app.include_router(router)
            client = TestClient(app)
            
            response = client.post(
                "/webhook/test",
                json={"test": "data"}
            )
            
            assert response.status_code == 404


class TestStatusUpdates:
    """Test cases for message status update processing"""
    
    @pytest.mark.asyncio
    async def test_process_status_update(self):
        """Should process message status updates"""
        with patch('app.api.webhooks.redis_client') as mock_redis:
            mock_redis.cache_set = AsyncMock(return_value=True)
            
            from app.api.webhooks import _process_status_updates
            
            statuses = [
                {
                    "id": "wamid.test123",
                    "status": "delivered",
                    "recipient_id": "15559876543"
                }
            ]
            
            await _process_status_updates(statuses)
            
            # Verify status was cached
            mock_redis.cache_set.assert_called()
    
    @pytest.mark.asyncio
    async def test_process_failed_status(self):
        """Should log failed message status"""
        with patch('app.api.webhooks.redis_client') as mock_redis, \
             patch('app.api.webhooks.logger') as mock_logger:
            
            mock_redis.cache_set = AsyncMock(return_value=True)
            
            from app.api.webhooks import _process_status_updates
            
            statuses = [
                {
                    "id": "wamid.test123",
                    "status": "failed",
                    "recipient_id": "15559876543",
                    "errors": [{
                        "code": 131047,
                        "message": "Message failed to send"
                    }]
                }
            ]
            
            await _process_status_updates(statuses)
            
            # Verify error was logged
            mock_logger.error.assert_called()
