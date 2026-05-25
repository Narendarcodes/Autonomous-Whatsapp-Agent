"""
Tests for Configuration and Settings
"""

import pytest
from unittest.mock import patch
import os


class TestSettings:
    """Test cases for Settings class"""
    
    def test_default_settings(self):
        """Should have correct default values"""
        with patch.dict(os.environ, {
            'WHATSAPP_TOKEN': 'test_token',
            'WHATSAPP_PHONE_ID': 'test_phone',
            'WHATSAPP_VERIFY_TOKEN': 'test_verify',
            'GOOGLE_CLIENT_ID': 'test_client_id',
            'GOOGLE_CLIENT_SECRET': 'test_secret'
        }, clear=False):
            from app.core.config import Settings
            settings = Settings()
            
            assert settings.APP_NAME == "WhatsApp AI Calendar Agent"
            assert settings.DEBUG is True
            assert settings.ENVIRONMENT == "development"
    
    def test_database_url_construction(self):
        """Should construct database URL correctly"""
        with patch.dict(os.environ, {
            'WHATSAPP_TOKEN': 'test',
            'WHATSAPP_PHONE_ID': 'test',
            'WHATSAPP_VERIFY_TOKEN': 'test',
            'GOOGLE_CLIENT_ID': 'test',
            'GOOGLE_CLIENT_SECRET': 'test',
            'POSTGRES_USER': 'testuser',
            'POSTGRES_PASSWORD': 'testpass',
            'POSTGRES_DB': 'testdb',
            'POSTGRES_HOST': 'localhost',
            'POSTGRES_PORT': '5432'
        }, clear=False):
            from app.core.config import Settings
            settings = Settings()
            
            assert "postgresql://" in settings.DATABASE_URL
            assert "testuser" in settings.DATABASE_URL
            assert "testdb" in settings.DATABASE_URL
    
    def test_async_database_url_construction(self):
        """Should construct async database URL correctly"""
        with patch.dict(os.environ, {
            'WHATSAPP_TOKEN': 'test',
            'WHATSAPP_PHONE_ID': 'test',
            'WHATSAPP_VERIFY_TOKEN': 'test',
            'GOOGLE_CLIENT_ID': 'test',
            'GOOGLE_CLIENT_SECRET': 'test'
        }, clear=False):
            from app.core.config import Settings
            settings = Settings()
            
            assert "postgresql+asyncpg://" in settings.ASYNC_DATABASE_URL
    
    def test_redis_url_with_password(self):
        """Should construct Redis URL with password"""
        with patch.dict(os.environ, {
            'WHATSAPP_TOKEN': 'test',
            'WHATSAPP_PHONE_ID': 'test',
            'WHATSAPP_VERIFY_TOKEN': 'test',
            'GOOGLE_CLIENT_ID': 'test',
            'GOOGLE_CLIENT_SECRET': 'test',
            'REDIS_HOST': 'redis.example.com',
            'REDIS_PORT': '6379',
            'REDIS_PASSWORD': 'secret123',
            'REDIS_DB': '0'
        }, clear=False):
            from app.core.config import Settings
            settings = Settings()
            
            assert "redis://" in settings.REDIS_URL
            assert "secret123" in settings.REDIS_URL
    
    def test_whatsapp_send_message_url(self):
        """Should construct WhatsApp send message URL correctly"""
        with patch.dict(os.environ, {
            'WHATSAPP_TOKEN': 'test',
            'WHATSAPP_PHONE_ID': '123456789',
            'WHATSAPP_VERIFY_TOKEN': 'test',
            'GOOGLE_CLIENT_ID': 'test',
            'GOOGLE_CLIENT_SECRET': 'test',
            'WHATSAPP_API_VERSION': 'v21.0',
            'WHATSAPP_API_URL': 'https://graph.facebook.com'
        }, clear=False):
            from app.core.config import Settings
            settings = Settings()
            
            expected = "https://graph.facebook.com/v21.0/123456789/messages"
            assert settings.WHATSAPP_SEND_MESSAGE_URL == expected


class TestTimeoutSettings:
    """Test cases for timeout configuration"""
    
    def test_default_timeout_values(self):
        """Should have correct default timeout values"""
        with patch.dict(os.environ, {
            'WHATSAPP_TOKEN': 'test',
            'WHATSAPP_PHONE_ID': 'test',
            'WHATSAPP_VERIFY_TOKEN': 'test',
            'GOOGLE_CLIENT_ID': 'test',
            'GOOGLE_CLIENT_SECRET': 'test'
        }, clear=False):
            from app.core.config import Settings
            settings = Settings()
            
            assert settings.HTTP_CONNECT_TIMEOUT == 10.0
            assert settings.HTTP_READ_TIMEOUT == 30.0
            assert settings.HTTP_WRITE_TIMEOUT == 30.0
            assert settings.HTTP_POOL_TIMEOUT == 10.0
    
    def test_circuit_breaker_settings(self):
        """Should have correct circuit breaker defaults"""
        with patch.dict(os.environ, {
            'WHATSAPP_TOKEN': 'test',
            'WHATSAPP_PHONE_ID': 'test',
            'WHATSAPP_VERIFY_TOKEN': 'test',
            'GOOGLE_CLIENT_ID': 'test',
            'GOOGLE_CLIENT_SECRET': 'test'
        }, clear=False):
            from app.core.config import Settings
            settings = Settings()
            
            assert settings.CIRCUIT_FAILURE_THRESHOLD == 5
            assert settings.CIRCUIT_SUCCESS_THRESHOLD == 2
            assert settings.CIRCUIT_TIMEOUT_SECONDS == 60
    
    def test_retry_settings(self):
        """Should have correct retry defaults"""
        with patch.dict(os.environ, {
            'WHATSAPP_TOKEN': 'test',
            'WHATSAPP_PHONE_ID': 'test',
            'WHATSAPP_VERIFY_TOKEN': 'test',
            'GOOGLE_CLIENT_ID': 'test',
            'GOOGLE_CLIENT_SECRET': 'test'
        }, clear=False):
            from app.core.config import Settings
            settings = Settings()
            
            assert settings.RETRY_MAX_ATTEMPTS == 3
            assert settings.RETRY_BASE_DELAY == 1.0
            assert settings.RETRY_MAX_DELAY == 10.0
    
    def test_redis_timeout_settings(self):
        """Should have correct Redis timeout defaults"""
        with patch.dict(os.environ, {
            'WHATSAPP_TOKEN': 'test',
            'WHATSAPP_PHONE_ID': 'test',
            'WHATSAPP_VERIFY_TOKEN': 'test',
            'GOOGLE_CLIENT_ID': 'test',
            'GOOGLE_CLIENT_SECRET': 'test'
        }, clear=False):
            from app.core.config import Settings
            settings = Settings()
            
            assert settings.REDIS_CONNECT_TIMEOUT == 5.0
            assert settings.REDIS_SOCKET_TIMEOUT == 5.0


class TestRateLimitSettings:
    """Test cases for rate limit configuration"""
    
    def test_rate_limit_defaults(self):
        """Should have correct rate limit defaults"""
        with patch.dict(os.environ, {
            'WHATSAPP_TOKEN': 'test',
            'WHATSAPP_PHONE_ID': 'test',
            'WHATSAPP_VERIFY_TOKEN': 'test',
            'GOOGLE_CLIENT_ID': 'test',
            'GOOGLE_CLIENT_SECRET': 'test'
        }, clear=False):
            from app.core.config import Settings
            settings = Settings()
            
            assert settings.RATE_LIMIT_REQUESTS == 10
            assert settings.RATE_LIMIT_WINDOW == 60


class TestCacheSettings:
    """Test cases for cache configuration"""
    
    def test_cache_ttl_defaults(self):
        """Should have correct cache TTL defaults"""
        with patch.dict(os.environ, {
            'WHATSAPP_TOKEN': 'test',
            'WHATSAPP_PHONE_ID': 'test',
            'WHATSAPP_VERIFY_TOKEN': 'test',
            'GOOGLE_CLIENT_ID': 'test',
            'GOOGLE_CLIENT_SECRET': 'test'
        }, clear=False):
            from app.core.config import Settings
            settings = Settings()
            
            assert settings.CACHE_TTL_SHORT == 300  # 5 minutes
            assert settings.CACHE_TTL_MEDIUM == 1800  # 30 minutes
            assert settings.CACHE_TTL_LONG == 3600  # 1 hour


class TestSessionSettings:
    """Test cases for session configuration"""
    
    def test_session_defaults(self):
        """Should have correct session defaults"""
        with patch.dict(os.environ, {
            'WHATSAPP_TOKEN': 'test',
            'WHATSAPP_PHONE_ID': 'test',
            'WHATSAPP_VERIFY_TOKEN': 'test',
            'GOOGLE_CLIENT_ID': 'test',
            'GOOGLE_CLIENT_SECRET': 'test'
        }, clear=False):
            from app.core.config import Settings
            settings = Settings()
            
            assert settings.SESSION_TTL == 3600
            assert settings.CONVERSATION_MAX_MESSAGES == 10
            assert settings.CONVERSATION_TTL == 3600


class TestAgentSettings:
    """Test cases for agent configuration"""
    
    def test_agent_defaults(self):
        """Should have correct agent defaults"""
        with patch.dict(os.environ, {
            'WHATSAPP_TOKEN': 'test',
            'WHATSAPP_PHONE_ID': 'test',
            'WHATSAPP_VERIFY_TOKEN': 'test',
            'GOOGLE_CLIENT_ID': 'test',
            'GOOGLE_CLIENT_SECRET': 'test'
        }, clear=False):
            from app.core.config import Settings
            settings = Settings()
            
            assert settings.AGENT_MAX_ITERATIONS == 5
            assert settings.AGENT_TIMEOUT == 30
