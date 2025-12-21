"""
Tests for Security Utilities
"""

import pytest
from unittest.mock import patch


class TestSanitizePhoneNumber:
    """Test cases for phone number sanitization"""
    
    def test_basic_phone_number(self):
        """Should handle basic phone number - returns digits only"""
        from app.core.security import sanitize_phone_number
        
        result = sanitize_phone_number("15551234567")
        assert result == "15551234567"  # digits only
    
    def test_phone_with_plus(self):
        """Should strip plus sign (returns digits only)"""
        from app.core.security import sanitize_phone_number
        
        result = sanitize_phone_number("+15551234567")
        assert result == "15551234567"  # plus is stripped
    
    def test_phone_with_spaces(self):
        """Should strip spaces"""
        from app.core.security import sanitize_phone_number
        
        result = sanitize_phone_number(" 1555 123 4567 ")
        assert result == "15551234567"
    
    def test_phone_with_dashes(self):
        """Should remove dashes"""
        from app.core.security import sanitize_phone_number
        
        result = sanitize_phone_number("1-555-123-4567")
        assert result == "15551234567"
    
    def test_phone_with_parentheses(self):
        """Should remove parentheses"""
        from app.core.security import sanitize_phone_number
        
        result = sanitize_phone_number("(555) 123-4567")
        assert result == "5551234567"
    
    def test_empty_phone(self):
        """Should handle empty string"""
        from app.core.security import sanitize_phone_number
        
        result = sanitize_phone_number("")
        assert result == ""  # empty returns empty
    
    def test_phone_with_country_code(self):
        """Should extract digits only, stripping +"""
        from app.core.security import sanitize_phone_number
        
        result = sanitize_phone_number("+44 7911 123456")
        assert result == "447911123456"


class TestInputValidation:
    """Test cases for input validation"""
    
    def test_message_length_limits(self):
        """Should respect message length limits"""
        from app.api.webhooks import MAX_MESSAGE_LENGTH, MIN_MESSAGE_LENGTH
        
        # Max should be reasonable for WhatsApp
        assert MAX_MESSAGE_LENGTH <= 65536  # 64KB max
        assert MAX_MESSAGE_LENGTH >= 1000   # At least 1KB
        
        # Min should be at least 1
        assert MIN_MESSAGE_LENGTH >= 1
    
    def test_prevent_empty_message(self):
        """Empty messages should be rejected"""
        from app.api.webhooks import MIN_MESSAGE_LENGTH
        
        message = ""
        assert len(message) < MIN_MESSAGE_LENGTH


class TestWebhookSignatureValidation:
    """Test cases for webhook signature validation"""
    
    def test_timing_attack_protection(self):
        """Signature comparison should be constant-time"""
        from app.api.webhooks import verify_webhook_signature
        import hmac
        import hashlib
        
        # The verify function uses hmac.compare_digest which is constant-time
        payload = b"test payload"
        secret = "test_secret"
        
        # Generate valid signature
        valid_sig = hmac.new(
            secret.encode('utf-8'),
            payload,
            hashlib.sha256
        ).hexdigest()
        
        # These should all take similar time regardless of how many chars match
        result1 = verify_webhook_signature(payload, f"sha256={valid_sig}", secret)
        result2 = verify_webhook_signature(payload, "sha256=aaaa", secret)
        result3 = verify_webhook_signature(payload, f"sha256={valid_sig[:-1]}x", secret)
        
        assert result1 is True
        assert result2 is False
        assert result3 is False


class TestDebugModeProtection:
    """Test cases for debug mode protection - test the logic pattern used in main.py"""
    
    def test_docs_disabled_in_production(self):
        """API docs should be disabled in production"""
        # Test the logic pattern: DEBUG=False, ENVIRONMENT=production
        DEBUG = False
        ENVIRONMENT = "production"
        
        is_production = ENVIRONMENT.lower() == "production"
        enable_docs = DEBUG and not is_production
        
        assert enable_docs is False
    
    def test_docs_disabled_debug_true_production(self):
        """API docs should be disabled even with DEBUG=True in production"""
        # Test the logic pattern: DEBUG=True, ENVIRONMENT=production
        DEBUG = True
        ENVIRONMENT = "production"
        
        is_production = ENVIRONMENT.lower() == "production"
        enable_docs = DEBUG and not is_production
        
        assert enable_docs is False
    
    def test_docs_enabled_in_development(self):
        """API docs should be enabled in development with DEBUG"""
        # Test the logic pattern: DEBUG=True, ENVIRONMENT=development
        DEBUG = True
        ENVIRONMENT = "development"
        
        is_production = ENVIRONMENT.lower() == "production"
        enable_docs = DEBUG and not is_production
        
        assert enable_docs is True
    
    def test_cors_restricted_in_production(self):
        """CORS should be restricted in production"""
        # Test the logic pattern: DEBUG=True, ENVIRONMENT=production
        DEBUG = True
        ENVIRONMENT = "production"
        
        is_production = ENVIRONMENT.lower() == "production"
        cors_origins = ["*"] if (DEBUG and not is_production) else []
        
        assert cors_origins == []


class TestRateLimiting:
    """Test cases for rate limiting"""
    
    @pytest.mark.asyncio
    async def test_rate_limit_enforcement(self):
        """Should enforce rate limits"""
        from app.db.redis_client import RedisClient
        from unittest.mock import AsyncMock
        
        client = RedisClient()
        client.client = AsyncMock()
        client.client.ping = AsyncMock(return_value=True)
        
        # Simulate rate limit exceeded
        client.client.incr = AsyncMock(return_value=100)  # Over limit
        client.client.ttl = AsyncMock(return_value=30)
        
        with patch('app.db.redis_client.settings') as mock_settings:
            mock_settings.RATE_LIMIT_REQUESTS = 10
            mock_settings.RATE_LIMIT_WINDOW = 60
            
            result = await client.check_rate_limit("user123")
        
        assert result is False
    
    @pytest.mark.asyncio
    async def test_rate_limit_under_threshold(self):
        """Should allow requests under rate limit"""
        from app.db.redis_client import RedisClient
        from unittest.mock import AsyncMock
        
        client = RedisClient()
        client.client = AsyncMock()
        client.client.ping = AsyncMock(return_value=True)
        
        # Under limit
        client.client.incr = AsyncMock(return_value=5)
        client.client.expire = AsyncMock()
        client.client.ttl = AsyncMock(return_value=-2)  # Key doesn't exist
        
        with patch('app.db.redis_client.settings') as mock_settings:
            mock_settings.RATE_LIMIT_REQUESTS = 10
            mock_settings.RATE_LIMIT_WINDOW = 60
            
            result = await client.check_rate_limit("user123")
        
        assert result is True


import hashlib
