"""
WhatsApp Webhook Endpoints
Handles incoming messages from WhatsApp Cloud API
"""

from fastapi import APIRouter, Request, Response, HTTPException, status, Query, Header
from fastapi.responses import PlainTextResponse
import json
import hmac
import hashlib
from typing import Optional

from app.core.config import settings
from app.core.logging import logger
from app.core.security import sanitize_phone_number
from app.schemas.message import WhatsAppWebhookPayload, MessageContext
from app.db.redis_client import redis_client
from app.infrastructure.redis_streams import RedisStreamProducer


router = APIRouter()

# Message validation constants
MAX_MESSAGE_LENGTH = 4096  # Maximum allowed message length
MIN_MESSAGE_LENGTH = 1     # Minimum message length (non-empty)


async def _process_status_updates(statuses: list) -> None:
    """
    Process message status updates (sent, delivered, read, failed)
    
    Args:
        statuses: List of status update objects
    """
    for status_update in statuses:
        # Handle both dict and Pydantic object
        if hasattr(status_update, 'id'):
            message_id = status_update.id
            status_type = status_update.status
            recipient_id = status_update.recipient_id
        else:
            message_id = status_update.get("id")
            status_type = status_update.get("status")
            recipient_id = status_update.get("recipient_id")
        
        logger.info(f"📊 Message status update: {message_id} -> {status_type}")
        
        # Store status in Redis for tracking
        if message_id and status_type:
            await redis_client.cache_set(
                key=f"msg_status:{message_id}",
                value=status_type,
                ttl=86400  # 24 hours
            )
        
        # Handle failed messages
        if status_type == "failed":
            if hasattr(status_update, 'errors'):
                errors = status_update.errors or []
            else:
                errors = status_update.get("errors", [])
            
            if errors:
                error = errors[0] if isinstance(errors, list) else errors
                error_code = error.get("code") if hasattr(error, 'get') else getattr(error, 'code', None)
                error_message = error.get("message") if hasattr(error, 'get') else getattr(error, 'message', None)
                logger.error(f"❌ Message {message_id} failed: {error_code} - {error_message}")


async def _process_interactive_message(message: dict, change: dict) -> None:
    """
    Process interactive message replies (button clicks, list selections)
    
    Args:
        message: Message object with interactive content
        change: Change object containing metadata
    """
    try:
        interactive_data = message.get("interactive", {})
        interactive_type = interactive_data.get("type")  # button_reply, list_reply
        
        user_phone = sanitize_phone_number(message.get("from", ""))
        message_id = message.get("id")
        
        if interactive_type == "button_reply":
            button_reply = interactive_data.get("button_reply", {})
            button_id = button_reply.get("id")
            button_title = button_reply.get("title")
            
            logger.info(f"🔘 Button clicked by {user_phone}: {button_title} (ID: {button_id})")
            
            # Process button click as a text message
            from app.services.message_router import message_router
            
            # Get user name if available
            user_name = None
            if change.get("contacts"):
                user_name = change["contacts"][0].get("profile", {}).get("name")
            
            await message_router.route_message(
                user_phone=user_phone,
                message_text=button_title,  # Use button title as message
                message_id=message_id,
                user_name=user_name
            )
        
        elif interactive_type == "list_reply":
            list_reply = interactive_data.get("list_reply", {})
            list_id = list_reply.get("id")
            list_title = list_reply.get("title")
            list_description = list_reply.get("description")
            
            logger.info(f"📋 List item selected by {user_phone}: {list_title}")
            
            # TODO: Implement list reply handling
            
    except Exception as e:
        logger.error(f"Error processing interactive message: {e}", exc_info=True)


def verify_webhook_signature(payload: bytes, signature: str, app_secret: str) -> bool:
    """
    Verify webhook payload signature from Meta
    
    Args:
        payload: Raw request body bytes
        signature: X-Hub-Signature-256 header value
        app_secret: WhatsApp App Secret
        
    Returns:
        True if signature is valid
    """
    if not signature or not app_secret:
        return False
    
    # Compute expected signature
    expected_signature = hmac.new(
        app_secret.encode('utf-8'),
        payload,
        hashlib.sha256
    ).hexdigest()
    
    # Remove 'sha256=' prefix if present
    if signature.startswith('sha256='):
        signature = signature[7:]
    
    # Constant-time comparison to prevent timing attacks
    return hmac.compare_digest(expected_signature, signature)


@router.get("/webhook")
async def verify_webhook(
    hub_mode: str = Query(alias="hub.mode"),
    hub_challenge: str = Query(alias="hub.challenge"),
    hub_verify_token: str = Query(alias="hub.verify_token")
):
    """
    WhatsApp webhook verification endpoint
    
    Meta will call this endpoint with verification parameters
    We must return the challenge if verify_token matches
    
    Args:
        hub_mode: Should be "subscribe"
        hub_challenge: Challenge string to return
        hub_verify_token: Token to verify (must match our WHATSAPP_VERIFY_TOKEN)
    
    Returns:
        Challenge string if verification successful
    """
    logger.info(f"📞 Webhook verification request received")
    logger.debug(f"Mode: {hub_mode}, Token: {hub_verify_token[:10]}...")
    
    # Verify the token
    if hub_mode == "subscribe" and hub_verify_token == settings.WHATSAPP_VERIFY_TOKEN:
        logger.info("✅ Webhook verification successful")
        return PlainTextResponse(content=hub_challenge, status_code=200)
    else:
        logger.warning("❌ Webhook verification failed - invalid token")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Verification failed"
        )


@router.post("/webhook")
async def receive_webhook(
    request: Request,
    x_hub_signature_256: Optional[str] = Header(None, alias="X-Hub-Signature-256")
):
    """
    WhatsApp webhook receiver endpoint (FAST - only queues messages)
    
    Receives incoming messages from WhatsApp and pushes to Redis Stream
    Agent Worker processes messages asynchronously
    
    Args:
        request: FastAPI request object with WhatsApp payload
        x_hub_signature_256: Webhook signature header (optional in dev mode)
    
    Returns:
        200 OK immediately after queuing message
    """
    try:
        # Get request body
        body = await request.body()
        body_str = body.decode('utf-8')
        
        logger.info("📨 Webhook message received")
        
        # Verify webhook signature (skip in debug mode)
        if not settings.DEBUG and settings.WHATSAPP_APP_SECRET:
            if not verify_webhook_signature(body, x_hub_signature_256 or "", settings.WHATSAPP_APP_SECRET):
                logger.warning("❌ Invalid webhook signature")
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid signature"
                )
        
        # Parse payload
        try:
            payload = json.loads(body_str)
            webhook_data = WhatsAppWebhookPayload(**payload)
        except Exception as e:
            logger.error(f"Failed to parse webhook payload: {e}")
            # Return 200 anyway to avoid retries
            return {"status": "ok"}
        
        # Initialize stream producer
        producer = RedisStreamProducer(redis_client)
        
        # Process each entry
        for entry in webhook_data.entry:
            for change in entry.changes:
                value = change.value
                
                # Handle message status updates (lightweight)
                if value.statuses:
                    await _process_status_updates(value.statuses)
                
                # Check if there are messages
                if not value.messages:
                    continue
                
                # Queue each message to Redis Stream
                for message in value.messages:
                    # Only process text messages
                    if message.type != "text" or not message.text:
                        logger.info(f"Skipping non-text message: {message.type}")
                        continue
                    
                    # Extract message details
                    user_phone = sanitize_phone_number(message.from_)
                    message_text = message.text.body
                    message_id = message.id
                    
                    # Validate message length
                    if not message_text or len(message_text) < MIN_MESSAGE_LENGTH:
                        logger.warning(f"⚠️ Empty or too short message from {user_phone} - skipping")
                        continue
                    
                    if len(message_text) > MAX_MESSAGE_LENGTH:
                        logger.warning(f"⚠️ Message too long ({len(message_text)} chars) from {user_phone} - truncating")
                        message_text = message_text[:MAX_MESSAGE_LENGTH]
                    
                    # Get user name if available
                    user_name = None
                    if value.contacts:
                        user_name = value.contacts[0].profile.name
                    
                    logger.info(f"📱 Queuing message from {user_phone}: {message_text[:50]}...")
                    
                    # Check for duplicate message (idempotency)
                    idempotency_key = f"msg_processed:{message_id}"
                    if await redis_client.cache_get(idempotency_key):
                        logger.info(f"⚠️ Duplicate message {message_id} - skipping")
                        continue
                    
                    # Mark message as being processed (TTL 24 hours)
                    await redis_client.cache_set(idempotency_key, "processing", ttl=86400)
                    
                    # Check rate limit
                    if not await redis_client.check_rate_limit(user_phone):
                        logger.warning(f"⚠️  Rate limit exceeded for user {user_phone}")
                        continue
                    
                    # Push to Redis Stream (FAST - no LLM, no DB writes)
                    # Worker will handle all heavy processing
                    stream_id = await producer.push_message(
                        user_id=user_phone,  # Will be resolved to UUID by worker
                        wa_phone=user_phone,
                        message_text=message_text,
                        message_id=message_id,
                        metadata={"user_name": user_name} if user_name else {}
                    )
                    
                    logger.info(f"✅ Message queued to stream: {stream_id}")
        
        # Return 200 OK immediately (worker processes asynchronously)
        return {"status": "ok"}
    
    except Exception as e:
        logger.error(f"❌ Error processing webhook: {e}", exc_info=True)
        # Return 200 to avoid retries
        return {"status": "error", "message": str(e)}


@router.post("/webhook/test")
async def test_webhook(request: Request):
    """
    Test endpoint for webhook simulation
    SECURITY: Only available in debug mode AND non-production environment
    """
    # SECURITY: Double-check both DEBUG mode and environment
    is_production = settings.ENVIRONMENT.lower() == "production"
    
    if not settings.DEBUG or is_production:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Not found"
        )
    
    body = await request.json()
    logger.info(f"🧪 Test webhook received: {json.dumps(body, indent=2)}")
    
    return {
        "status": "test_ok",
        "received": body
    }
