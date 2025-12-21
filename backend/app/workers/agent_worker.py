"""
Agent Worker
Background worker that consumes messages from Redis Stream and processes them
"""

import asyncio
import signal
import sys
from typing import Dict, Any
from datetime import datetime
from sqlalchemy import select

from app.core.config import settings
from app.core.logging import logger
from app.db.database import get_async_session
from app.db.redis_client import redis_client
from app.infrastructure.redis_streams import RedisStreamConsumer
from app.models.user import User
from app.services.agent_engine import agent_engine
from app.services.decision_resolver import decision_resolver
from app.services.conflict_detection import ConflictDetectionService
from app.services.whatsapp_service import whatsapp_service


class AgentWorker:
    """Worker that processes incoming messages from Redis Stream"""
    
    def __init__(self):
        self.running = False
        self.consumer: RedisStreamConsumer = None
        self.conflict_service = ConflictDetectionService()
        self._processing_count = 0  # Track in-flight messages
        self._shutdown_timeout = 30  # seconds to wait for in-flight messages
    
    async def start(self):
        """Start the worker"""
        try:
            logger.info("🚀 Starting Agent Worker...")
            
            # Connect to Redis
            await redis_client.connect()

            # Initialize Redis Stream consumer
            self.consumer = RedisStreamConsumer(
                redis_client=redis_client,
                stream_name="message_queue",
                group_name="agent_workers",
                consumer_name=f"agent_worker_{settings.ENVIRONMENT}_{datetime.utcnow().timestamp()}"
            )
            
            # Ensure consumer group exists
            await self.consumer.ensure_group()
            
            # Setup signal handlers for graceful shutdown
            signal.signal(signal.SIGINT, self._handle_shutdown)
            signal.signal(signal.SIGTERM, self._handle_shutdown)
            
            self.running = True
            logger.info("✅ Agent Worker started successfully")
            
            # Main processing loop
            await self._process_loop()
            
        except Exception as e:
            logger.error(f"Failed to start Agent Worker: {e}")
            sys.exit(1)
    
    async def _process_loop(self):
        """Main message processing loop"""
        
        while self.running:
            try:
                # Check for pending messages first (recovery from crashes)
                pending_messages = await self.consumer.read_pending_messages(count=10)
                
                if pending_messages:
                    logger.info(f"🔄 Processing {len(pending_messages)} pending message(s)")
                    for message_id, message_data in pending_messages:
                        await self._process_message(message_id, message_data)
                
                # Read new messages (blocks for 5 seconds)
                messages = await self.consumer.read_messages()
                
                if messages:
                    for message_id, message_data in messages:
                        await self._process_message(message_id, message_data)
                
                # Cleanup: trim stream periodically
                if datetime.utcnow().minute % 10 == 0:  # Every 10 minutes
                    await self.consumer.trim_stream(max_len=10000)
                
            except Exception as e:
                logger.error(f"Error in processing loop: {e}")
                await asyncio.sleep(5)  # Back off on errors
    
    async def _process_message(self, message_id: str, message_data: Dict[str, Any]):
        """
        Process a single message
        
        Args:
            message_id: Redis Stream message ID
            message_data: Message payload
        """
        self._processing_count += 1
        try:
            user_id = message_data.get("user_id")
            wa_phone = message_data.get("wa_phone")
            message_text = message_data.get("message_text")
            
            logger.info(f"📨 Processing message {message_id} from {wa_phone}")
            
            # Get database session
            async for db in get_async_session():
                try:
                    # Load user
                    query = select(User).where(User.wa_phone == wa_phone)
                    result = await db.execute(query)
                    user = result.scalar_one_or_none()
                    
                    if not user:
                        logger.info(f"New user detected: {wa_phone}, sending OAuth registration link")
                        
                        # Generate direct Google OAuth URL
                        from app.services.oauth_service import oauth_service
                        try:
                            oauth_url, state = await oauth_service.generate_authorization_url(
                                user_phone=wa_phone,
                                db=db
                            )
                            
                            welcome_message = (
                                f"🔐 *Calendar Authorization Required*\n\n"
                                f"To manage your Google Calendar, I need your permission to access it.\n\n"
                                f"Please click the link below to authorize:\n"
                                f"{oauth_url}\n\n"
                                f"This is a one-time setup and your data is completely secure. "
                                f"Once authorized, you can start managing your calendar through WhatsApp!"
                            )
                        except Exception as e:
                            logger.error(f"Failed to generate OAuth URL: {e}")
                            welcome_message = (
                                f"👋 Welcome to WhatsApp Calendar Agent!\n\n"
                                f"To get started, please visit:\n"
                                f"{settings.BASE_URL}/oauth/start?phone={wa_phone}\n\n"
                                f"and connect your Google Calendar."
                            )
                        
                        await whatsapp_service.send_text_message(
                            to=wa_phone,
                            message=welcome_message
                        )
                        await self.consumer.ack_message(message_id)
                        return
                    
                    # Check if user has pending decision
                    pending_decision = await self.conflict_service.get_active_pending_decision(
                        db=db,
                        user_id=user.id
                    )
                    
                    if pending_decision:
                        # Route to decision resolver
                        logger.info(f"🔀 Routing to decision resolver (pending_decision_id={pending_decision.id})")
                        
                        response_text = await decision_resolver.process_decision_response(
                            db=db,
                            user=user,
                            message_text=message_text,
                            pending_decision=pending_decision
                        )
                    else:
                        # Route to normal agent
                        logger.info(f"🤖 Routing to agent engine")
                        
                        response_text = await agent_engine.process_message(
                            user=user,
                            message=message_text,
                            db=db
                        )
                    
                    # Send response via WhatsApp
                    success = await whatsapp_service.send_text_message(
                        to=wa_phone,
                        message=response_text
                    )
                    
                    if success:
                        logger.info(f"✅ Message {message_id} processed successfully")
                        await self.consumer.ack_message(message_id)
                    else:
                        logger.error(f"Failed to send WhatsApp response for message {message_id}")
                        # Don't ACK - will be retried
                        await self.consumer.nack_message(message_id)
                    
                except Exception as e:
                    logger.error(f"Error processing message {message_id}: {e}", exc_info=True)
                    # Don't ACK - message will be retried
                    await self.consumer.nack_message(message_id)
                    
                    # Send error message to user
                    try:
                        await whatsapp_service.send_text_message(
                            to=wa_phone,
                            message="❌ An error occurred processing your request. Please try again."
                        )
                    except:
                        pass
                
                finally:
                    await db.close()
        
        except Exception as e:
            logger.error(f"Critical error processing message {message_id}: {e}")
            await self.consumer.nack_message(message_id)
        finally:
            self._processing_count -= 1
    
    def _handle_shutdown(self, signum, frame):
        """Handle shutdown signals gracefully"""
        logger.info(f"🛑 Received shutdown signal ({signum})")
        self.running = False
    
    async def stop(self):
        """Stop the worker gracefully, waiting for in-flight messages"""
        logger.info("🛑 Stopping Agent Worker...")
        self.running = False
        
        # Wait for in-flight messages to complete
        if self._processing_count > 0:
            logger.info(f"⏳ Waiting for {self._processing_count} in-flight message(s) to complete...")
            start_time = datetime.utcnow()
            while self._processing_count > 0:
                if (datetime.utcnow() - start_time).total_seconds() > self._shutdown_timeout:
                    logger.warning(f"⚠️ Shutdown timeout reached with {self._processing_count} messages still processing")
                    break
                await asyncio.sleep(0.5)
        
        # Close connections
        try:
            await redis_client.disconnect()
        except Exception as e:
            logger.error(f"Error closing Redis connection: {e}")
        
        try:
            await whatsapp_service.close()
        except Exception as e:
            logger.error(f"Error closing WhatsApp client: {e}")
        
        logger.info("✅ Agent Worker stopped")


async def main():
    """Main entry point for Agent Worker"""
    print("🔄 Agent Worker script started", flush=True)
    try:
        worker = AgentWorker()
        print("✅ Agent Worker initialized", flush=True)
        await worker.start()
    except KeyboardInterrupt:
        logger.info("🛑 Interrupted by user")
    except Exception as e:
        print(f"❌ CRITICAL ERROR IN WORKER MAIN: {e}", flush=True)
        logger.exception("Critical error in worker main")
    finally:
        if 'worker' in locals():
            await worker.stop()

if __name__ == "__main__":
    print("🚀 Launching Agent Worker...", flush=True)
    asyncio.run(main())
