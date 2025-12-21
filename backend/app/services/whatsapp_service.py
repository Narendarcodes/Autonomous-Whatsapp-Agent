"""
WhatsApp Service
Handles sending messages via WhatsApp Cloud API
"""

import httpx
from typing import Dict, Any, List, Optional, Tuple, Type

from app.core.config import settings
from app.core.logging import logger
from app.core.circuit_breaker import CircuitBreaker, CircuitState
from app.core.retry import retry_with_backoff


# Exceptions that should trigger retry (transient errors)
RETRYABLE_EXCEPTIONS: Tuple[Type[Exception], ...] = (
    httpx.ConnectTimeout,
    httpx.ReadTimeout,
    httpx.ConnectError,
    httpx.PoolTimeout,
)


class CircuitOpenError(Exception):
    """Raised when circuit breaker is open"""
    pass


class WhatsAppService:
    """Service for sending messages via WhatsApp Cloud API with resilience patterns"""
    
    def __init__(self):
        self.api_url = settings.WHATSAPP_SEND_MESSAGE_URL
        self.access_token = settings.WHATSAPP_TOKEN
        self.phone_id = settings.WHATSAPP_PHONE_ID
        # Persistent HTTP client with connection pooling
        self._client: Optional[httpx.AsyncClient] = None
        # Circuit breaker for WhatsApp API (using configurable settings)
        self._circuit_breaker = CircuitBreaker(
            name="whatsapp_api",
            failure_threshold=settings.CIRCUIT_FAILURE_THRESHOLD,
            success_threshold=settings.CIRCUIT_SUCCESS_THRESHOLD,
            timeout=settings.CIRCUIT_TIMEOUT_SECONDS
        )
    
    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create persistent HTTP client with connection pooling"""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(
                    connect=settings.HTTP_CONNECT_TIMEOUT,
                    read=settings.HTTP_READ_TIMEOUT,
                    write=settings.HTTP_WRITE_TIMEOUT,
                    pool=settings.HTTP_POOL_TIMEOUT
                ),
                limits=httpx.Limits(max_keepalive_connections=5, max_connections=10)
            )
        return self._client
    
    async def close(self):
        """Close the HTTP client"""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None
        
    def _get_headers(self) -> Dict[str, str]:
        """Get API request headers"""
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json"
        }
    
    def _check_circuit(self) -> None:
        """Check if circuit breaker allows execution"""
        if not self._circuit_breaker.can_execute():
            logger.warning(f"Circuit breaker OPEN for WhatsApp API - request blocked")
            raise CircuitOpenError("WhatsApp API circuit breaker is open - service temporarily unavailable")
    
    async def _make_request(self, payload: Dict[str, Any]) -> httpx.Response:
        """
        Make HTTP request to WhatsApp API with retry logic
        
        Args:
            payload: Request payload
            
        Returns:
            HTTP response
        """
        client = await self._get_client()
        return await client.post(
            self.api_url,
            headers=self._get_headers(),
            json=payload
        )
    
    async def _send_with_resilience(
        self,
        payload: Dict[str, Any],
        operation_name: str
    ) -> bool:
        """
        Send message with circuit breaker and retry
        
        Args:
            payload: WhatsApp API payload
            operation_name: Name of operation for logging
            
        Returns:
            True if successful
        """
        # Check circuit breaker first
        try:
            self._check_circuit()
        except CircuitOpenError:
            return False
        
        try:
            # Retry with exponential backoff for transient errors (using configurable settings)
            response = await retry_with_backoff(
                self._make_request,
                payload,
                max_retries=settings.RETRY_MAX_ATTEMPTS,
                base_delay=settings.RETRY_BASE_DELAY,
                max_delay=settings.RETRY_MAX_DELAY,
                retryable_exceptions=RETRYABLE_EXCEPTIONS
            )
            
            if response.status_code == 200:
                self._circuit_breaker.record_success()
                result = response.json()
                message_id = result.get("messages", [{}])[0].get("id")
                logger.info(f"✅ {operation_name} successful (ID: {message_id})")
                return True
            elif response.status_code >= 500:
                # Server errors should trigger circuit breaker
                self._circuit_breaker.record_failure()
                logger.error(f"WhatsApp API server error ({response.status_code}): {response.text}")
                return False
            else:
                # Client errors (4xx) don't trigger circuit breaker (not transient)
                logger.error(f"WhatsApp API client error ({response.status_code}): {response.text}")
                return False
                
        except RETRYABLE_EXCEPTIONS as e:
            # All retries exhausted for network errors
            self._circuit_breaker.record_failure()
            logger.error(f"WhatsApp API network error after retries: {e}")
            return False
        except Exception as e:
            # Unexpected errors
            self._circuit_breaker.record_failure()
            logger.error(f"Unexpected error in {operation_name}: {e}")
            return False
    
    def get_circuit_status(self) -> Dict[str, Any]:
        """Get circuit breaker status for monitoring"""
        return {
            "state": self._circuit_breaker.state.value,
            "failure_count": self._circuit_breaker.failure_count,
            "success_count": self._circuit_breaker.success_count,
            "last_failure_time": self._circuit_breaker.last_failure_time.isoformat() if self._circuit_breaker.last_failure_time else None
        }
    
    async def send_text_message(
        self,
        to: str,
        message: str
    ) -> bool:
        """
        Send a text message to user with resilience patterns
        
        Args:
            to: Recipient phone number (with country code)
            message: Message text to send
            
        Returns:
            True if successful
        """
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "text",
            "text": {
                "preview_url": False,
                "body": message
            }
        }
        
        logger.info(f"📤 Sending WhatsApp message to {to}")
        logger.debug(f"Message preview: {message[:100]}...")
        
        return await self._send_with_resilience(payload, "Text message sent")
    
    async def send_button_message(
        self,
        to: str,
        text: str,
        buttons: List[Dict[str, str]]
    ) -> bool:
        """
        Send a message with interactive buttons with resilience patterns
        
        Args:
            to: Recipient phone number
            text: Message text
            buttons: List of button dictionaries with 'id' and 'title'
            
        Returns:
            True if successful
        """
        # Format buttons (max 3 buttons)
        button_components = []
        for btn in buttons[:3]:
            button_components.append({
                "type": "reply",
                "reply": {
                    "id": btn.get("id", "btn_id"),
                    "title": btn.get("title", "Button")[:20]  # Max 20 chars
                }
            })
        
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "interactive",
            "interactive": {
                "type": "button",
                "body": {
                    "text": text
                },
                "action": {
                    "buttons": button_components
                }
            }
        }
        
        logger.info(f"📤 Sending button message to {to}")
        
        return await self._send_with_resilience(payload, "Button message sent")
    
    async def send_list_message(
        self,
        to: str,
        header: str,
        body: str,
        footer: str,
        button_text: str,
        sections: List[Dict[str, Any]]
    ) -> bool:
        """
        Send a message with interactive list (menu) with resilience patterns
        
        Args:
            to: Recipient phone number
            header: Header text (optional)
            body: Body text
            footer: Footer text (optional)
            button_text: Button text to show list (e.g., "View Options")
            sections: List of sections, each with title and rows
                Example: [
                    {
                        "title": "Today's Events",
                        "rows": [
                            {"id": "event_1", "title": "Morning Meeting", "description": "9:00 AM"},
                            {"id": "event_2", "title": "Lunch", "description": "12:30 PM"}
                        ]
                    }
                ]
            
        Returns:
            True if successful
        """
        # Format sections (max 10 sections, 10 rows per section)
        formatted_sections = []
        for section in sections[:10]:
            rows = []
            for row in section.get("rows", [])[:10]:
                rows.append({
                    "id": row.get("id", "row_id"),
                    "title": row.get("title", "Option")[:24],  # Max 24 chars
                    "description": row.get("description", "")[:72]  # Max 72 chars
                })
            
            formatted_sections.append({
                "title": section.get("title", "Options")[:24],
                "rows": rows
            })
        
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "interactive",
            "interactive": {
                "type": "list",
                "header": {
                    "type": "text",
                    "text": header[:60] if header else "Options"
                },
                "body": {
                    "text": body[:1024]
                },
                "footer": {
                    "text": footer[:60] if footer else ""
                },
                "action": {
                    "button": button_text[:20],
                    "sections": formatted_sections
                }
            }
        }
        
        logger.info(f"📤 Sending list message to {to}")
        
        return await self._send_with_resilience(payload, "List message sent")
    
    async def mark_as_read(self, message_id: str) -> bool:
        """
        Mark a message as read with resilience patterns
        
        Args:
            message_id: WhatsApp message ID
            
        Returns:
            True if successful
        """
        payload = {
            "messaging_product": "whatsapp",
            "status": "read",
            "message_id": message_id
        }
        
        # mark_as_read is non-critical, don't retry aggressively
        try:
            self._check_circuit()
        except CircuitOpenError:
            logger.warning(f"Circuit open - skipping mark_as_read for {message_id}")
            return False
        
        try:
            client = await self._get_client()
            response = await client.post(
                self.api_url,
                headers=self._get_headers(),
                json=payload
            )
            
            if response.status_code == 200:
                self._circuit_breaker.record_success()
                logger.debug(f"Message {message_id} marked as read")
                return True
            else:
                # Don't record failure for mark_as_read - it's non-critical
                logger.warning(f"Failed to mark message as read: {response.status_code}")
                return False
                
        except Exception as e:
            logger.error(f"Error marking message as read: {e}")
            return False
    
    async def send_oauth_prompt(self, to: str, auth_url: str) -> bool:
        """
        Send OAuth authorization prompt to user
        
        Args:
            to: Recipient phone number
            auth_url: OAuth authorization URL
            
        Returns:
            True if successful
        """
        message = f"""🔐 **Calendar Authorization Required**

To manage your Google Calendar, I need your permission to access it.

Please click the link below to authorize:
{auth_url}

This is a one-time setup and your data is completely secure. Once authorized, you can start managing your calendar through WhatsApp!"""
        
        return await self.send_text_message(to, message)
    
    async def send_oauth_success(self, to: str) -> bool:
        """
        Send OAuth success confirmation
        
        Args:
            to: Recipient phone number
            
        Returns:
            True if successful
        """
        message = """✅ **Authorization Successful!**

Your Google Calendar is now connected! 🎉

You can now:
📅 Check your schedule ("What's on my calendar today?")
➕ Create events ("Schedule meeting tomorrow at 2pm")
✏️ Update events ("Reschedule my 3pm meeting to 4pm")
🗑️ Delete events ("Cancel my dentist appointment")

Just send me a message and I'll help you manage your calendar!"""
        
        return await self.send_text_message(to, message)
    
    def format_error_message(self, error: str) -> str:
        """
        Format error message for user
        
        Args:
            error: Error description
            
        Returns:
            User-friendly error message
        """
        return f"""❌ **Oops! Something went wrong**

{error}

Please try again or rephrase your request. If the problem persists, please contact support."""


# Global instance
whatsapp_service = WhatsAppService()
