"""
Message Router
Orchestrates the flow from WhatsApp message to agent to response
"""

from sqlalchemy.orm import Session
from typing import Optional

from app.core.logging import logger
from app.core.security import sanitize_phone_number
from app.models.user import User, AuditLog
from app.services.oauth_service import oauth_service
from app.services.agent_engine import agent_engine
from app.services.whatsapp_service import whatsapp_service
from app.db.database import SessionLocal


class MessageRouter:
    """Routes and processes incoming WhatsApp messages"""
    
    async def route_message(
        self,
        user_phone: str,
        message_text: str,
        message_id: str,
        user_name: Optional[str] = None
    ) -> bool:
        """
        Route and process incoming message
        
        Args:
            user_phone: User's WhatsApp phone number
            message_text: Message text
            message_id: WhatsApp message ID
            user_name: User's display name (optional)
            
        Returns:
            True if processed successfully
        """
        db = SessionLocal()
        
        try:
            # Sanitize phone number
            user_phone = sanitize_phone_number(user_phone)
            
            logger.info(f"📨 Routing message from {user_phone}")
            logger.debug(f"Message: {message_text[:100]}...")
            
            # Mark message as read
            await whatsapp_service.mark_as_read(message_id)
            
            # Get or create user
            user = self._get_or_create_user(user_phone, db)
            
            # Log incoming message
            self._log_action(
                user=user,
                action="message_received",
                details={"message": message_text[:200], "message_id": message_id},
                status="success",
                db=db
            )
            
            # Check OAuth status
            has_oauth = oauth_service.check_oauth_status(user)
            
            if not has_oauth:
                logger.info(f"🔐 User {user_phone} needs OAuth authorization")
                await self._handle_oauth_required(user, db)
                return True
            
            # Process with agent
            logger.info(f"🤖 Processing message with agent")
            response_text = await agent_engine.process_message(
                user=user,
                message=message_text,
                db=db
            )
            
            # Send response
            success = await whatsapp_service.send_text_message(
                to=user_phone,
                message=response_text
            )
            
            if success:
                # Log outgoing message
                self._log_action(
                    user=user,
                    action="message_sent",
                    details={"message": response_text[:200]},
                    status="success",
                    db=db
                )
                logger.info(f"✅ Message routing complete for {user_phone}")
            else:
                logger.error(f"Failed to send response to {user_phone}")
                self._log_action(
                    user=user,
                    action="message_sent",
                    details={"error": "Failed to send"},
                    status="failure",
                    db=db
                )
            
            return success
            
        except Exception as e:
            logger.error(f"Message routing error: {e}", exc_info=True)
            
            # Try to send error message to user
            try:
                error_msg = whatsapp_service.format_error_message(
                    "I encountered an error processing your request. Please try again."
                )
                await whatsapp_service.send_text_message(user_phone, error_msg)
            except:
                pass
            
            # Log error
            if 'user' in locals():
                self._log_action(
                    user=user,
                    action="message_routing_error",
                    details={"error": str(e)},
                    status="failure",
                    db=db
                )
            
            return False
            
        finally:
            db.close()
    
    async def _handle_oauth_required(self, user: User, db: Session) -> None:
        """
        Handle OAuth authorization flow for user
        
        Args:
            user: User object
            db: Database session
        """
        try:
            # Generate authorization URL
            auth_url, state = await oauth_service.generate_authorization_url(
                user_phone=user.wa_phone,
                db=db
            )
            
            # Send OAuth prompt to user
            success = await whatsapp_service.send_oauth_prompt(
                to=user.wa_phone,
                auth_url=auth_url
            )
            
            if success:
                self._log_action(
                    user=user,
                    action="oauth_prompt_sent",
                    details={"state": state},
                    status="success",
                    db=db
                )
            else:
                logger.error(f"Failed to send OAuth prompt to {user.wa_phone}")
                
        except Exception as e:
            logger.error(f"OAuth handling error: {e}")
            
            # Send generic error
            error_msg = """I need to connect to your Google Calendar, but I encountered an error. Please try again later."""
            await whatsapp_service.send_text_message(user.wa_phone, error_msg)
    
    def _get_or_create_user(self, phone: str, db: Session) -> User:
        """
        Get existing user or create new one
        
        Args:
            phone: Phone number
            db: Database session
            
        Returns:
            User object
        """
        from sqlalchemy import select
        query = select(User).where(User.wa_phone == phone)
        result = db.execute(query)
        user = result.scalar_one_or_none()
        
        if not user:
            logger.info(f"Creating new user: {phone}")
            user = User(wa_phone=phone, is_active=True)
            db.add(user)
            db.flush()
            db.refresh(user)
            
            # Log user creation
            self._log_action(
                user=user,
                action="user_created",
                details={"phone": phone},
                status="success",
                db=db
            )
        
        return user
    
    def _log_action(
        self,
        user: User,
        action: str,
        details: dict,
        status: str,
        db: Session
    ) -> None:
        """
        Log action to audit log
        
        Args:
            user: User object
            action: Action name
            details: Action details
            status: success or failure
            db: Database session
        """
        try:
            audit_log = AuditLog(
                user_id=user.id,
                action=action,
                details=details,
                status=status
            )
            db.add(audit_log)
            db.flush()
        except Exception as e:
            logger.error(f"Failed to log action: {e}")


# Global instance
message_router = MessageRouter()
