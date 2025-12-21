"""
Redis Streams Infrastructure
Producer/Consumer utilities for message queueing
"""

import asyncio
import json
import time
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime

from redis.asyncio import Redis
from redis.exceptions import ResponseError

from app.core.logging import logger
from app.core.config import settings


class RedisStreamProducer:
    """Producer for pushing messages into Redis Streams"""
    
    def __init__(self, redis_client: Any):
        """
        Initialize producer
        
        Args:
            redis_client: Redis connection instance or RedisClient wrapper
        """
        self.redis_client = redis_client
        self.stream_name = "message_queue"
        self.scheduled_stream = "scheduled_jobs_stream"

    @property
    def client(self) -> Redis:
        """Get the underlying Redis client"""
        if hasattr(self.redis_client, 'client'):
            return self.redis_client.client
        return self.redis_client
    
    async def push_message(
        self,
        user_id: str,
        wa_phone: str,
        message_text: str,
        message_id: str,
        metadata: Optional[Dict[str, Any]] = None
    ) -> str:
        """
        Push incoming WhatsApp message to stream
        
        Args:
            user_id: User UUID
            wa_phone: WhatsApp phone number
            message_text: Message content
            message_id: WhatsApp message ID (for idempotency)
            metadata: Additional metadata
            
        Returns:
            Stream message ID
        """
        try:
            payload = {
                "user_id": user_id,
                "wa_phone": wa_phone,
                "message_text": message_text,
                "message_id": message_id,
                "timestamp": datetime.utcnow().isoformat(),
                "metadata": json.dumps(metadata or {})
            }
            
            # Validate Redis client
            if not self.client:
                raise ValueError("Redis client is not connected")
            
            # XADD returns message ID (e.g., '1234567890123-0')
            stream_id = await self.client.xadd(self.stream_name, payload, maxlen=10000)
            
            logger.info(f"📤 Pushed message to stream: {stream_id} (user={wa_phone})")
            return stream_id.decode() if isinstance(stream_id, bytes) else stream_id
            
        except Exception as e:
            logger.error(f"Failed to push message to stream: {e}")
            raise
    
    async def push_scheduled_job(
        self,
        job_type: str,
        user_id: str,
        payload: Dict[str, Any]
    ) -> str:
        """
        Push scheduled job notification to stream
        
        Args:
            job_type: Job type (reminder, morning_briefing, etc.)
            user_id: User UUID
            payload: Job payload
            
        Returns:
            Stream message ID
        """
        try:
            message = {
                "job_type": job_type,
                "user_id": user_id,
                "payload": json.dumps(payload),
                "timestamp": datetime.utcnow().isoformat()
            }
            
            stream_id = await self.client.xadd(self.scheduled_stream, message, maxlen=10000)
            
            logger.info(f"📤 Pushed scheduled job to stream: {job_type} (user={user_id})")
            return stream_id.decode() if isinstance(stream_id, bytes) else stream_id
            
        except Exception as e:
            logger.error(f"Failed to push scheduled job: {e}")
            raise


class RedisStreamConsumer:
    """Consumer for reading messages from Redis Streams with consumer groups"""
    
    def __init__(
        self,
        redis_client: Any,
        stream_name: str,
        group_name: str,
        consumer_name: str
    ):
        """
        Initialize consumer
        
        Args:
            redis_client: Redis connection instance or RedisClient wrapper
            stream_name: Stream to consume from
            group_name: Consumer group name
            consumer_name: Unique consumer identifier
        """
        self.redis_client = redis_client
        self.stream_name = stream_name
        self.group_name = group_name
        self.consumer_name = consumer_name
        self.block_ms = 5000  # Block for 5 seconds waiting for messages
        self.count = 1  # Process one message at a time for reliability

    @property
    def client(self) -> Redis:
        """Get the underlying Redis client"""
        if hasattr(self.redis_client, 'client'):
            return self.redis_client.client
        return self.redis_client
    
    async def ensure_group(self) -> None:
        """Create consumer group if it doesn't exist"""
        try:
            # XGROUP CREATE creates group, reads from beginning with ID '0'
            await self.client.xgroup_create(
                name=self.stream_name,
                groupname=self.group_name,
                id='0',
                mkstream=True
            )
            logger.info(f"✅ Created consumer group: {self.group_name} on {self.stream_name}")
        except ResponseError as e:
            if "BUSYGROUP" in str(e):
                logger.debug(f"Consumer group {self.group_name} already exists")
            else:
                logger.error(f"Failed to create consumer group: {e}")
                raise
    
    async def read_messages(self) -> List[Tuple[str, Dict[str, Any]]]:
        """
        Read new messages from stream (blocking)
        
        Returns:
            List of (message_id, message_data) tuples
        """
        try:
            # XREADGROUP reads from consumer group, '>' means only new messages
            response = await self.client.xreadgroup(
                groupname=self.group_name,
                consumername=self.consumer_name,
                streams={self.stream_name: '>'},
                count=self.count,
                block=self.block_ms
            )
            
            if not response:
                return []
            
            messages = []
            for stream_name, stream_messages in response:
                for message_id, message_data in stream_messages:
                    # Decode bytes to strings
                    decoded_data = {
                        k.decode() if isinstance(k, bytes) else k: 
                        v.decode() if isinstance(v, bytes) else v
                        for k, v in message_data.items()
                    }
                    messages.append((
                        message_id.decode() if isinstance(message_id, bytes) else message_id,
                        decoded_data
                    ))
            
            if messages:
                logger.debug(f"📥 Read {len(messages)} message(s) from {self.stream_name}")
            
            return messages
            
        except asyncio.TimeoutError:
            # Timeout during blocking read is normal - no messages available
            logger.debug(f"No new messages in {self.stream_name} (timeout)")
            return []
        except Exception as e:
            # Only log unexpected errors
            if "Timeout" in str(e) or "timeout" in str(e):
                # Timeout is expected when no messages are available
                logger.debug(f"No new messages in {self.stream_name}")
            else:
                logger.error(f"Failed to read from stream: {e}")
            return []
    
    async def read_pending_messages(self, count: int = 10) -> List[Tuple[str, Dict[str, Any]]]:
        """
        Read pending messages that were delivered but not acknowledged
        Useful for recovering from crashes
        
        Args:
            count: Maximum messages to read
            
        Returns:
            List of (message_id, message_data) tuples
        """
        try:
            # XPENDING shows pending messages
            pending_info = await self.client.xpending_range(
                name=self.stream_name,
                groupname=self.group_name,
                min='-',
                max='+',
                count=count,
                consumername=self.consumer_name
            )
            
            if not pending_info:
                return []
            
            logger.info(f"🔄 Found {len(pending_info)} pending message(s)")
            
            # XCLAIM claims ownership of pending messages
            message_ids = [msg['message_id'] for msg in pending_info]
            response = await self.client.xclaim(
                name=self.stream_name,
                groupname=self.group_name,
                consumername=self.consumer_name,
                min_idle_time=5000,  # 5 seconds
                message_ids=message_ids
            )
            
            messages = []
            for message_id, message_data in response:
                decoded_data = {
                    k.decode() if isinstance(k, bytes) else k:
                    v.decode() if isinstance(v, bytes) else v
                    for k, v in message_data.items()
                }
                messages.append((
                    message_id.decode() if isinstance(message_id, bytes) else message_id,
                    decoded_data
                ))
            
            return messages
            
        except Exception as e:
            logger.error(f"Failed to read pending messages: {e}")
            return []
    
    async def ack_message(self, message_id: str) -> bool:
        """
        Acknowledge successful message processing
        
        Args:
            message_id: Message ID to acknowledge
            
        Returns:
            True if acknowledged successfully
        """
        try:
            # XACK marks message as processed
            result = await self.client.xack(self.stream_name, self.group_name, message_id)
            
            if result:
                logger.debug(f"✅ ACK message: {message_id}")
            else:
                logger.warning(f"⚠️ ACK failed for message: {message_id}")
            
            return bool(result)
            
        except Exception as e:
            logger.error(f"Failed to ACK message {message_id}: {e}")
            return False
    
    async def nack_message(self, message_id: str) -> bool:
        """
        Negative acknowledgment - message will be redelivered
        Use when processing fails and should be retried
        
        Args:
            message_id: Message ID to NACK
            
        Returns:
            True if operation successful
        """
        try:
            # Don't ACK - message remains in pending list
            # Optionally, we could use XACK and then re-add to stream
            logger.warning(f"❌ NACK message (will retry): {message_id}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to NACK message {message_id}: {e}")
            return False
    
    async def trim_stream(self, max_len: int = 10000) -> int:
        """
        Trim stream to maximum length (oldest messages removed)
        
        Args:
            max_len: Maximum stream length
            
        Returns:
            Number of messages trimmed
        """
        try:
            # XTRIM removes old messages
            result = await self.client.xtrim(self.stream_name, maxlen=max_len, approximate=True)
            
            if result > 0:
                logger.info(f"🗑️ Trimmed {result} old messages from {self.stream_name}")
            
            return result
            
        except Exception as e:
            logger.error(f"Failed to trim stream: {e}")
            return 0
    
    async def get_stream_info(self) -> Dict[str, Any]:
        """
        Get stream information and statistics
        
        Returns:
            Stream info dictionary
        """
        try:
            info = await self.client.xinfo_stream(self.stream_name)
            
            # Decode bytes in response
            decoded_info = {}
            for k, v in info.items():
                key = k.decode() if isinstance(k, bytes) else k
                value = v.decode() if isinstance(v, bytes) else v
                decoded_info[key] = value
            
            return decoded_info
            
        except Exception as e:
            logger.error(f"Failed to get stream info: {e}")
            return {}
