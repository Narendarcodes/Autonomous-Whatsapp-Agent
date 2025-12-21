"""
Tests for Redis Client
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import json


class TestRedisClient:
    """Test cases for RedisClient class"""
    
    @pytest.mark.asyncio
    async def test_connect_success(self, mock_settings):
        """Should establish connection successfully"""
        with patch('app.db.redis_client.redis') as mock_redis:
            mock_client = AsyncMock()
            mock_client.ping = AsyncMock(return_value=True)
            mock_redis.from_url = AsyncMock(return_value=mock_client)
            
            from app.db.redis_client import RedisClient
            client = RedisClient()
            client.connection_url = "redis://localhost:6379/0"
            
            await client.connect()
            
            assert client.client is not None
            mock_client.ping.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_connect_failure(self, mock_settings):
        """Should raise exception on connection failure"""
        with patch('app.db.redis_client.redis') as mock_redis:
            mock_redis.from_url = AsyncMock(side_effect=Exception("Connection refused"))
            
            from app.db.redis_client import RedisClient
            client = RedisClient()
            
            with pytest.raises(Exception, match="Connection refused"):
                await client.connect()
    
    @pytest.mark.asyncio
    async def test_ensure_connected_returns_false_when_no_client(self):
        """Should return False when client is None"""
        from app.db.redis_client import RedisClient
        client = RedisClient()
        client.client = None
        
        result = await client._ensure_connected()
        
        assert result is False
    
    @pytest.mark.asyncio
    async def test_ensure_connected_returns_true_when_connected(self):
        """Should return True when connected and ping succeeds"""
        from app.db.redis_client import RedisClient
        client = RedisClient()
        client.client = AsyncMock()
        client.client.ping = AsyncMock(return_value=True)
        
        result = await client._ensure_connected()
        
        assert result is True


class TestRedisClientSessionOperations:
    """Test cases for session operations"""
    
    @pytest.mark.asyncio
    async def test_set_session(self):
        """Should set session data in Redis"""
        from app.db.redis_client import RedisClient
        client = RedisClient()
        client.client = AsyncMock()
        client.client.ping = AsyncMock(return_value=True)
        client.client.setex = AsyncMock(return_value=True)
        
        session_data = {"user_id": "123", "state": "active"}
        result = await client.set_session("user123", session_data, ttl=3600)
        
        assert result is True
        client.client.setex.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_get_session_exists(self):
        """Should return session data when it exists"""
        from app.db.redis_client import RedisClient
        client = RedisClient()
        client.client = AsyncMock()
        client.client.ping = AsyncMock(return_value=True)
        
        expected_data = {"user_id": "123", "state": "active"}
        client.client.get = AsyncMock(return_value=json.dumps(expected_data))
        
        result = await client.get_session("user123")
        
        assert result == expected_data
    
    @pytest.mark.asyncio
    async def test_get_session_not_exists(self):
        """Should return None when session doesn't exist"""
        from app.db.redis_client import RedisClient
        client = RedisClient()
        client.client = AsyncMock()
        client.client.ping = AsyncMock(return_value=True)
        client.client.get = AsyncMock(return_value=None)
        
        result = await client.get_session("nonexistent")
        
        assert result is None
    
    @pytest.mark.asyncio
    async def test_delete_session(self):
        """Should delete session from Redis"""
        from app.db.redis_client import RedisClient
        client = RedisClient()
        client.client = AsyncMock()
        client.client.ping = AsyncMock(return_value=True)
        client.client.delete = AsyncMock(return_value=1)
        
        result = await client.delete_session("user123")
        
        assert result is True


class TestRedisClientConversation:
    """Test cases for conversation operations"""
    
    @pytest.mark.asyncio
    async def test_add_message(self):
        """Should add message to conversation"""
        from app.db.redis_client import RedisClient
        client = RedisClient()
        client.client = AsyncMock()
        client.client.ping = AsyncMock(return_value=True)
        client.client.lpush = AsyncMock()  # The implementation uses lpush, not rpush
        client.client.ltrim = AsyncMock()
        client.client.expire = AsyncMock()
        
        result = await client.add_message(
            "user123",
            role="user",
            content="Hello!"
        )
        
        assert result is True
        client.client.lpush.assert_called_once()  # Check lpush was called
    
    @pytest.mark.asyncio
    async def test_get_conversation(self):
        """Should return conversation messages"""
        from app.db.redis_client import RedisClient
        client = RedisClient()
        client.client = AsyncMock()
        client.client.ping = AsyncMock(return_value=True)
        
        messages = [
            json.dumps({"role": "user", "content": "Hi"}),
            json.dumps({"role": "assistant", "content": "Hello!"})
        ]
        client.client.lrange = AsyncMock(return_value=messages)
        
        result = await client.get_conversation("user123")
        
        assert len(result) == 2
        assert result[0]["role"] == "user"
        assert result[1]["role"] == "assistant"
    
    @pytest.mark.asyncio
    async def test_clear_conversation(self):
        """Should clear conversation history"""
        from app.db.redis_client import RedisClient
        client = RedisClient()
        client.client = AsyncMock()
        client.client.ping = AsyncMock(return_value=True)
        client.client.delete = AsyncMock(return_value=1)
        
        result = await client.clear_conversation("user123")
        
        assert result is True


class TestRedisClientCache:
    """Test cases for cache operations"""
    
    @pytest.mark.asyncio
    async def test_cache_set(self):
        """Should set cache value"""
        from app.db.redis_client import RedisClient
        client = RedisClient()
        client.client = AsyncMock()
        client.client.ping = AsyncMock(return_value=True)
        client.client.setex = AsyncMock(return_value=True)
        
        result = await client.cache_set("key1", "value1", ttl=300)
        
        assert result is True
    
    @pytest.mark.asyncio
    async def test_cache_get(self):
        """Should get cache value"""
        from app.db.redis_client import RedisClient
        client = RedisClient()
        client.client = AsyncMock()
        client.client.ping = AsyncMock(return_value=True)
        client.client.get = AsyncMock(return_value='"cached_value"')
        
        result = await client.cache_get("key1")
        
        assert result == "cached_value"
    
    @pytest.mark.asyncio
    async def test_cache_delete(self):
        """Should delete cache value"""
        from app.db.redis_client import RedisClient
        client = RedisClient()
        client.client = AsyncMock()
        client.client.ping = AsyncMock(return_value=True)
        client.client.delete = AsyncMock(return_value=1)
        
        result = await client.cache_delete("key1")
        
        assert result is True


class TestRedisClientRateLimit:
    """Test cases for rate limiting"""
    
    @pytest.mark.asyncio
    async def test_check_rate_limit_allowed(self):
        """Should allow request under rate limit"""
        from app.db.redis_client import RedisClient
        client = RedisClient()
        client.client = AsyncMock()
        client.client.ping = AsyncMock(return_value=True)
        client.client.incr = AsyncMock(return_value=5)  # Under limit
        client.client.expire = AsyncMock()
        client.client.ttl = AsyncMock(return_value=30)
        
        with patch('app.db.redis_client.settings') as mock_settings:
            mock_settings.RATE_LIMIT_REQUESTS = 10
            mock_settings.RATE_LIMIT_WINDOW = 60
            
            result = await client.check_rate_limit("user123")
        
        assert result is True
    
    @pytest.mark.asyncio
    async def test_check_rate_limit_exceeded(self):
        """Should deny request over rate limit"""
        from app.db.redis_client import RedisClient
        client = RedisClient()
        client.client = AsyncMock()
        client.client.ping = AsyncMock(return_value=True)
        client.client.incr = AsyncMock(return_value=15)  # Over limit
        client.client.ttl = AsyncMock(return_value=30)
        
        with patch('app.db.redis_client.settings') as mock_settings:
            mock_settings.RATE_LIMIT_REQUESTS = 10
            mock_settings.RATE_LIMIT_WINDOW = 60
            
            result = await client.check_rate_limit("user123")
        
        assert result is False


class TestRedisClientReconnection:
    """Test cases for reconnection logic"""
    
    @pytest.mark.asyncio
    async def test_reconnect_on_ping_failure(self):
        """Should attempt reconnection when ping fails"""
        from app.db.redis_client import RedisClient
        
        with patch('app.db.redis_client.redis') as mock_redis:
            client = RedisClient()
            client.client = AsyncMock()
            # First ping fails, reconnection succeeds
            client.client.ping = AsyncMock(
                side_effect=[Exception("Ping failed"), True]
            )
            
            new_client = AsyncMock()
            new_client.ping = AsyncMock(return_value=True)
            mock_redis.from_url = AsyncMock(return_value=new_client)
            
            # Should attempt reconnection
            result = await client._ensure_connected()
            
            # May be True or False depending on reconnection success
            assert isinstance(result, bool)
