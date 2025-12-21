"""
Redis Client
Handles caching, sessions, and conversation context
"""

import asyncio
import redis.asyncio as redis
from typing import Optional, Any
import json
from datetime import timedelta, datetime
from app.core.config import settings
from app.core.logging import logger


class RedisClient:
    """Async Redis client for caching and session management"""
    
    def __init__(self):
        self.client: Optional[redis.Redis] = None
        self.connection_url = settings.REDIS_URL
        self._reconnect_lock = asyncio.Lock()
        self._max_reconnect_attempts = 5
        self._reconnect_delay = settings.REDIS_CONNECT_TIMEOUT  # Use configurable timeout
        
    async def connect(self):
        """Establish Redis connection with configurable timeouts"""
        try:
            self.client = await redis.from_url(
                self.connection_url,
                encoding="utf-8",
                decode_responses=True,
                max_connections=10,
                socket_connect_timeout=settings.REDIS_CONNECT_TIMEOUT,
                socket_timeout=settings.REDIS_SOCKET_TIMEOUT,
                socket_keepalive=True,
                retry_on_timeout=True  # Enable auto-retry on timeout
            )
            await self.client.ping()
            logger.info("✅ Redis connection established")
        except Exception as e:
            logger.error(f"❌ Redis connection failed: {e}")
            raise
    
    async def _ensure_connected(self) -> bool:
        """
        Ensure Redis is connected, attempt reconnection if needed
        
        Returns:
            True if connected, False if reconnection failed
        """
        if self.client is None:
            return False
        
        try:
            await self.client.ping()
            return True
        except Exception:
            # Connection lost, attempt reconnection
            async with self._reconnect_lock:
                # Double-check after acquiring lock
                try:
                    await self.client.ping()
                    return True
                except Exception:
                    pass
                
                # Attempt reconnection with backoff
                for attempt in range(self._max_reconnect_attempts):
                    try:
                        logger.warning(f"Redis reconnection attempt {attempt + 1}/{self._max_reconnect_attempts}")
                        await self.connect()
                        logger.info("✅ Redis reconnected successfully")
                        return True
                    except Exception as e:
                        delay = self._reconnect_delay * (2 ** attempt)
                        logger.error(f"Reconnection failed: {e}. Retrying in {delay}s...")
                        await asyncio.sleep(delay)
                
                logger.error("❌ Failed to reconnect to Redis after all attempts")
                return False
    
    async def _with_connection(self, operation_name: str = "operation"):
        """
        Decorator-style helper to ensure connection before operations.
        Call this at the start of methods that need Redis.
        
        Raises:
            ConnectionError if Redis is unavailable
        """
        if not await self._ensure_connected():
            raise ConnectionError(f"Redis unavailable for {operation_name}")
    
    async def disconnect(self):
        """Close Redis connection"""
        if self.client:
            await self.client.close()
            logger.info("🔌 Redis connection closed")
    
    async def ping(self) -> bool:
        """Check if Redis is responsive"""
        try:
            if not await self._ensure_connected():
                return False
            return await self.client.ping()
        except Exception as e:
            logger.error(f"Redis ping failed: {e}")
            return False
    
    # ==================== SESSION MANAGEMENT ====================
    
    async def set_session(self, user_id: str, data: dict, ttl: Optional[int] = None) -> bool:
        """
        Store user session data
        
        Args:
            user_id: WhatsApp user phone number
            data: Session data dictionary
            ttl: Time-to-live in seconds (default from settings)
            
        Returns:
            True if successful
        """
        key = f"session:{user_id}"
        ttl = ttl or settings.SESSION_TTL
        
        try:
            if not await self._ensure_connected():
                return False
            await self.client.setex(
                key,
                ttl,
                json.dumps(data)
            )
            logger.debug(f"Session saved for user {user_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to set session: {e}")
            return False
    
    async def get_session(self, user_id: str) -> Optional[dict]:
        """
        Retrieve user session data
        
        Args:
            user_id: WhatsApp user phone number
            
        Returns:
            Session data dictionary or None
        """
        key = f"session:{user_id}"
        try:
            if not await self._ensure_connected():
                return None
            data = await self.client.get(key)
            if data:
                logger.debug(f"Session retrieved for user {user_id}")
                return json.loads(data)
            return None
        except Exception as e:
            logger.error(f"Failed to get session: {e}")
            return None
    
    async def update_session(self, user_id: str, data: dict, ttl: Optional[int] = None) -> bool:
        """
        Update existing session (merge with existing data)
        
        Args:
            user_id: WhatsApp user phone number
            data: New data to merge
            ttl: Time-to-live in seconds
            
        Returns:
            True if successful
        """
        existing = await self.get_session(user_id)
        if existing:
            existing.update(data)
            return await self.set_session(user_id, existing, ttl)
        else:
            return await self.set_session(user_id, data, ttl)
    
    async def delete_session(self, user_id: str) -> bool:
        """Delete user session"""
        key = f"session:{user_id}"
        try:
            if not await self._ensure_connected():
                return False
            await self.client.delete(key)
            logger.debug(f"Session deleted for user {user_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to delete session: {e}")
            return False
    
    # ==================== CONVERSATION CONTEXT ====================
    
    async def add_message(
        self,
        user_id: str,
        role: str,
        content: str,
        max_messages: Optional[int] = None
    ) -> bool:
        """
        Add message to conversation history
        
        Args:
            user_id: WhatsApp user phone number
            role: Message role (user/assistant)
            content: Message content
            max_messages: Maximum messages to keep (default from settings)
            
        Returns:
            True if successful
        """
        key = f"conversation:{user_id}"
        max_messages = max_messages or settings.CONVERSATION_MAX_MESSAGES
        
        message = json.dumps({
            "role": role,
            "content": content,
            "timestamp": datetime.utcnow().isoformat()
        })
        
        try:
            if not await self._ensure_connected():
                return False
            # Add to list (newest first)
            await self.client.lpush(key, message)
            
            # Trim to keep only recent messages
            await self.client.ltrim(key, 0, max_messages - 1)
            
            # Set expiry
            await self.client.expire(key, settings.CONVERSATION_TTL)
            
            logger.debug(f"Message added to conversation for user {user_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to add message: {e}")
            return False
    
    async def get_conversation(self, user_id: str) -> list:
        """
        Get conversation history (newest first)
        
        Args:
            user_id: WhatsApp user phone number
            
        Returns:
            List of message dictionaries
        """
        key = f"conversation:{user_id}"
        try:
            if not await self._ensure_connected():
                return []
            messages = await self.client.lrange(key, 0, -1)
            conversation = [json.loads(msg) for msg in messages]
            logger.debug(f"Retrieved {len(conversation)} messages for user {user_id}")
            return conversation
        except Exception as e:
            logger.error(f"Failed to get conversation: {e}")
            return []
    
    async def clear_conversation(self, user_id: str) -> bool:
        """Clear conversation history"""
        key = f"conversation:{user_id}"
        try:
            if not await self._ensure_connected():
                return False
            await self.client.delete(key)
            logger.debug(f"Conversation cleared for user {user_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to clear conversation: {e}")
            return False
    
    # ==================== CACHE MANAGEMENT ====================
    
    async def cache_set(self, key: str, value: Any, ttl: int = 300) -> bool:
        """
        Generic cache set
        
        Args:
            key: Cache key
            value: Value to cache (will be JSON serialized)
            ttl: Time-to-live in seconds
            
        Returns:
            True if successful
        """
        try:
            if not await self._ensure_connected():
                return False
            await self.client.setex(
                f"cache:{key}",
                ttl,
                json.dumps(value)
            )
            return True
        except Exception as e:
            logger.error(f"Cache set failed for key {key}: {e}")
            return False
    
    async def cache_get(self, key: str) -> Optional[Any]:
        """
        Generic cache get
        
        Args:
            key: Cache key
            
        Returns:
            Cached value or None
        """
        try:
            if not await self._ensure_connected():
                return None
            data = await self.client.get(f"cache:{key}")
            return json.loads(data) if data else None
        except Exception as e:
            logger.error(f"Cache get failed for key {key}: {e}")
            return None
    
    async def cache_delete(self, key: str) -> bool:
        """Delete cache entry"""
        try:
            if not await self._ensure_connected():
                return False
            await self.client.delete(f"cache:{key}")
            return True
        except Exception as e:
            logger.error(f"Cache delete failed for key {key}: {e}")
            return False
    
    # ==================== RATE LIMITING ====================
    
    async def check_rate_limit(
        self,
        user_id: str,
        max_requests: Optional[int] = None,
        window: Optional[int] = None
    ) -> bool:
        """
        Check if user exceeded rate limit
        
        Args:
            user_id: WhatsApp user phone number
            max_requests: Maximum requests per window (default from settings)
            window: Time window in seconds (default from settings)
            
        Returns:
            True if within rate limit, False if exceeded
        """
        max_requests = max_requests or settings.RATE_LIMIT_REQUESTS
        window = window or settings.RATE_LIMIT_WINDOW
        
        key = f"rate_limit:{user_id}"
        
        try:
            if not await self._ensure_connected():
                return False  # Fail closed
            current = await self.client.incr(key)
            
            if current == 1:
                await self.client.expire(key, window)
            
            if current > max_requests:
                logger.warning(f"Rate limit exceeded for user {user_id}")
                return False
            
            return True
        except Exception as e:
            logger.error(f"Rate limit check failed: {e}")
            # Fail closed: deny requests when Redis is unavailable
            # This prevents potential abuse during outages
            return False
    
    # ==================== OAUTH TOKEN CACHE ====================
    
    async def cache_oauth_token(self, user_id: str, access_token: str, ttl: int = 3600) -> bool:
        """Cache OAuth access token"""
        key = f"oauth:{user_id}"
        try:
            if not await self._ensure_connected():
                return False
            await self.client.setex(key, ttl, access_token)
            logger.debug(f"OAuth token cached for user {user_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to cache OAuth token: {e}")
            return False
    
    async def get_oauth_token(self, user_id: str) -> Optional[str]:
        """Get cached OAuth token"""
        key = f"oauth:{user_id}"
        try:
            if not await self._ensure_connected():
                return None
            token = await self.client.get(key)
            if token:
                logger.debug(f"OAuth token retrieved from cache for user {user_id}")
            return token
        except Exception as e:
            logger.error(f"Failed to get OAuth token: {e}")
            return None


# Global instance
redis_client = RedisClient()
