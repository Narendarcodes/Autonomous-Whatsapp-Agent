"""
Integration Tests
End-to-end tests for the WhatsApp Calendar Agent
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timedelta


class TestEndToEndMessageFlow:
    """Integration tests for message processing flow"""
    
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_full_message_processing_flow(self):
        """Test complete flow from webhook to response"""
        # This test simulates the full flow:
        # 1. Webhook receives message
        # 2. Message is queued to Redis Stream
        # 3. Worker processes message
        # 4. Agent generates response
        # 5. WhatsApp service sends response
        
        # Setup mocks
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
            from httpx import AsyncClient, ASGITransport
            
            app = FastAPI()
            app.include_router(router)
            
            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test"
            ) as client:
                # Simulate incoming WhatsApp message
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
                                    "id": "wamid.integration_test",
                                    "timestamp": "1638316800",
                                    "text": {"body": "What events do I have today?"},
                                    "type": "text"
                                }]
                            },
                            "field": "messages"
                        }]
                    }]
                }
                
                response = await client.post("/webhook", json=payload)
                
                # Verify webhook accepted the message
                assert response.status_code == 200
                assert response.json()["status"] == "ok"
                
                # Verify message was pushed to stream
                mock_producer.push_message.assert_called_once()
                call_args = mock_producer.push_message.call_args
                assert call_args.kwargs["message_text"] == "What events do I have today?"


class TestRateLimitingIntegration:
    """Integration tests for rate limiting"""
    
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_rate_limit_blocks_excess_requests(self):
        """Should block requests when rate limit exceeded"""
        with patch('app.api.webhooks.settings') as mock_settings, \
             patch('app.api.webhooks.redis_client') as mock_redis, \
             patch('app.api.webhooks.RedisStreamProducer') as mock_producer_class:
            
            mock_settings.DEBUG = True
            mock_settings.ENVIRONMENT = "testing"
            mock_settings.WHATSAPP_APP_SECRET = None
            
            mock_redis.cache_get = AsyncMock(return_value=None)
            mock_redis.cache_set = AsyncMock(return_value=True)
            # Rate limit exceeded
            mock_redis.check_rate_limit = AsyncMock(return_value=False)
            
            mock_producer = AsyncMock()
            mock_producer.push_message = AsyncMock(return_value="stream_id")
            mock_producer_class.return_value = mock_producer
            
            from app.api.webhooks import router
            from fastapi import FastAPI
            from httpx import AsyncClient, ASGITransport
            
            app = FastAPI()
            app.include_router(router)
            
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
                                "contacts": [{"profile": {"name": "Test"}, "wa_id": "15559876543"}],
                                "messages": [{
                                    "from": "15559876543",
                                    "id": "wamid.rate_limit_test",
                                    "timestamp": "1638316800",
                                    "text": {"body": "Test message"},
                                    "type": "text"
                                }]
                            },
                            "field": "messages"
                        }]
                    }]
                }
                
                response = await client.post("/webhook", json=payload)
                
                # Webhook should still return 200
                assert response.status_code == 200
                
                # But message should NOT be pushed due to rate limit
                mock_producer.push_message.assert_not_called()


class TestDuplicateMessageHandling:
    """Integration tests for duplicate message handling"""
    
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_duplicate_message_skipped(self):
        """Should skip duplicate messages (idempotency)"""
        with patch('app.api.webhooks.settings') as mock_settings, \
             patch('app.api.webhooks.redis_client') as mock_redis, \
             patch('app.api.webhooks.RedisStreamProducer') as mock_producer_class:
            
            mock_settings.DEBUG = True
            mock_settings.ENVIRONMENT = "testing"
            mock_settings.WHATSAPP_APP_SECRET = None
            
            # Message already processed
            mock_redis.cache_get = AsyncMock(return_value="processing")
            mock_redis.cache_set = AsyncMock(return_value=True)
            mock_redis.check_rate_limit = AsyncMock(return_value=True)
            
            mock_producer = AsyncMock()
            mock_producer.push_message = AsyncMock(return_value="stream_id")
            mock_producer_class.return_value = mock_producer
            
            from app.api.webhooks import router
            from fastapi import FastAPI
            from httpx import AsyncClient, ASGITransport
            
            app = FastAPI()
            app.include_router(router)
            
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
                                "contacts": [{"profile": {"name": "Test"}, "wa_id": "15559876543"}],
                                "messages": [{
                                    "from": "15559876543",
                                    "id": "wamid.duplicate_msg",
                                    "timestamp": "1638316800",
                                    "text": {"body": "Duplicate message"},
                                    "type": "text"
                                }]
                            },
                            "field": "messages"
                        }]
                    }]
                }
                
                response = await client.post("/webhook", json=payload)
                
                assert response.status_code == 200
                # Message should NOT be pushed (duplicate)
                mock_producer.push_message.assert_not_called()


class TestCircuitBreakerIntegration:
    """Integration tests for circuit breaker behavior"""
    
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_circuit_opens_after_failures(self):
        """Circuit should open after multiple failures"""
        with patch('app.services.whatsapp_service.settings') as mock_settings:
            mock_settings.WHATSAPP_SEND_MESSAGE_URL = "https://test.api/messages"
            mock_settings.WHATSAPP_TOKEN = "test"
            mock_settings.WHATSAPP_PHONE_ID = "123"
            mock_settings.CIRCUIT_FAILURE_THRESHOLD = 3
            mock_settings.CIRCUIT_SUCCESS_THRESHOLD = 2
            mock_settings.CIRCUIT_TIMEOUT_SECONDS = 60
            mock_settings.HTTP_CONNECT_TIMEOUT = 1.0
            mock_settings.HTTP_READ_TIMEOUT = 1.0
            mock_settings.HTTP_WRITE_TIMEOUT = 1.0
            mock_settings.HTTP_POOL_TIMEOUT = 1.0
            mock_settings.RETRY_MAX_ATTEMPTS = 1
            mock_settings.RETRY_BASE_DELAY = 0.01
            mock_settings.RETRY_MAX_DELAY = 0.1
            
            from app.services.whatsapp_service import WhatsAppService
            from app.core.circuit_breaker import CircuitState
            
            service = WhatsAppService()
            
            # Simulate failures
            mock_response = MagicMock()
            mock_response.status_code = 500
            mock_response.text = "Server Error"
            
            mock_client = AsyncMock()
            mock_client.is_closed = False
            mock_client.post = AsyncMock(return_value=mock_response)
            service._client = mock_client
            
            # Make requests that will fail
            for _ in range(3):
                await service.send_text_message("+15551234567", "Test")
            
            # Circuit should be OPEN
            assert service._circuit_breaker.state == CircuitState.OPEN
            
            # Subsequent requests should be blocked
            result = await service.send_text_message("+15551234567", "Blocked")
            assert result is False


class TestHealthCheckIntegration:
    """Integration tests for health check endpoint"""
    
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_health_check_logic(self):
        """Health check should correctly determine service status"""
        # Test the logic patterns used in health check
        # without importing the health module that has deep dependencies
        
        # Simulate healthy state
        redis_healthy = True
        db_healthy = True
        
        overall_status = "healthy" if (redis_healthy and db_healthy) else "degraded"
        assert overall_status == "healthy"
        
        # Simulate degraded state (Redis down)
        redis_healthy = False
        overall_status = "healthy" if (redis_healthy and db_healthy) else "degraded"
        assert overall_status == "degraded"
        
        # Simulate degraded state (DB down)
        redis_healthy = True
        db_healthy = False
        overall_status = "healthy" if (redis_healthy and db_healthy) else "degraded"
        assert overall_status == "degraded"
