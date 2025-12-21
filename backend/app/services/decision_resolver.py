"""
Decision Resolver Service
Handles multi-turn conflict resolution workflow
"""

import json
from typing import Dict, Any, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models.user import User, PendingDecision, EventCache
from app.services.conflict_detection import ConflictDetectionService
from app.services.llm_factory import llm_service
from app.services.whatsapp_service import whatsapp_service
from app.core.logging import logger


class DecisionResolverService:
    """Service for resolving pending user decisions via LLM"""
    
    def __init__(self):
        self.conflict_service = ConflictDetectionService()
    
    async def process_decision_response(
        self,
        db: AsyncSession,
        user: User,
        message_text: str,
        pending_decision: PendingDecision
    ) -> str:
        """
        Process user's response to conflict resolution prompt
        
        Args:
            db: Database session
            user: User object
            message_text: User's response message
            pending_decision: Active pending decision
            
        Returns:
            Response message to send back to user
        """
        try:
            # Load events
            new_event_query = select(EventCache).where(EventCache.id == pending_decision.event_id)
            conflict_event_query = select(EventCache).where(EventCache.id == pending_decision.conflict_event_id)
            
            new_event_result = await db.execute(new_event_query)
            conflict_event_result = await db.execute(conflict_event_query)
            
            new_event = new_event_result.scalar_one_or_none()
            conflict_event = conflict_event_result.scalar_one_or_none()
            
            if not new_event or not conflict_event:
                logger.error(f"Events not found for pending decision {pending_decision.id}")
                return "❌ Error: Events not found. Please try creating your event again."
            
            # Build LLM prompt for decision parsing
            decision_prompt = self._build_decision_prompt(
                message_text,
                new_event,
                conflict_event,
                pending_decision.llm_suggestion
            )
            
            # Call LLM to parse user's intent
            logger.info(f"🤖 Calling LLM to parse decision for user {user.wa_phone}")
            
            response = await llm_service.chat_completion(
                messages=[
                    {"role": "system", "content": decision_prompt},
                    {"role": "user", "content": message_text}
                ],
                tools=None
            )
            
            message_content = response.get("message", {}).get("content", "")
            
            # Parse LLM response (expects JSON)
            try:
                decision_data = json.loads(message_content)
                decision = decision_data.get("decision")
            except json.JSONDecodeError:
                # Fallback: parse simple text responses
                decision = self._parse_text_decision(message_text)
            
            # Execute decision
            if decision == "keep_event_a":  # Keep new event
                success = await self.conflict_service.resolve_conflict(
                    db=db,
                    pending_decision=pending_decision,
                    keep_new_event=True
                )
                
                if success:
                    return f"✅ **Decision Confirmed**\n\nKept: **{new_event.summary}**\n📅 {new_event.start_time.strftime('%B %d at %I:%M %p')}\n\nCancelled: {conflict_event.summary}"
                else:
                    return "❌ Error processing your decision. Please try again."
            
            elif decision == "keep_event_b":  # Keep existing event
                success = await self.conflict_service.resolve_conflict(
                    db=db,
                    pending_decision=pending_decision,
                    keep_new_event=False
                )
                
                if success:
                    return f"✅ **Decision Confirmed**\n\nKept: **{conflict_event.summary}**\n📅 {conflict_event.start_time.strftime('%B %d at %I:%M %p')}\n\nCancelled: {new_event.summary}"
                else:
                    return "❌ Error processing your decision. Please try again."
            
            elif decision == "cancel_both":
                # Cancel pending decision (both events remain tentative)
                success = await self.conflict_service.cancel_pending_decision(db, pending_decision)
                
                if success:
                    return "❌ **Both Events Cancelled**\n\nYou can schedule new events whenever you're ready."
                else:
                    return "❌ Error cancelling events. Please try again."
            
            else:
                # Unclear response - ask again
                return self._generate_clarification_message(new_event, conflict_event)
            
        except Exception as e:
            logger.error(f"Error processing decision response: {e}")
            return "❌ Error processing your decision. Please reply with 'keep new', 'keep existing', or 'cancel'."
    
    def _build_decision_prompt(
        self,
        user_message: str,
        new_event: EventCache,
        conflict_event: EventCache,
        llm_suggestion: str
    ) -> str:
        """Build system prompt for decision parsing"""
        
        return f"""You are a calendar conflict resolution assistant. The user has two conflicting events and needs to choose which to keep.

**Event A (New):** {new_event.summary}
Time: {new_event.start_time.strftime('%B %d at %I:%M %p')} - {new_event.end_time.strftime('%I:%M %p')}

**Event B (Existing):** {conflict_event.summary}
Time: {conflict_event.start_time.strftime('%B %d at %I:%M %p')} - {conflict_event.end_time.strftime('%I:%M %p')}

Previous suggestion: {llm_suggestion}

Parse the user's message and determine their decision. Return ONLY a JSON object with this exact format:
{{
  "decision": "keep_event_a" | "keep_event_b" | "cancel_both" | "unclear"
}}

Rules:
- "keep new", "1", "first one", "the new one" → keep_event_a
- "keep existing", "2", "second one", "the old one" → keep_event_b  
- "cancel", "neither", "delete both" → cancel_both
- Anything else → unclear

Return ONLY the JSON, no other text."""
    
    def _parse_text_decision(self, message_text: str) -> str:
        """
        Fallback text parser for simple responses
        
        Args:
            message_text: User's message
            
        Returns:
            Decision string
        """
        message_lower = message_text.lower().strip()
        
        # Keep new event patterns
        if any(keyword in message_lower for keyword in ["keep new", "new one", "first", "1", "keep the new"]):
            return "keep_event_a"
        
        # Keep existing event patterns
        if any(keyword in message_lower for keyword in ["keep existing", "existing", "old one", "second", "2", "keep the existing"]):
            return "keep_event_b"
        
        # Cancel both patterns
        if any(keyword in message_lower for keyword in ["cancel", "neither", "delete", "remove both"]):
            return "cancel_both"
        
        return "unclear"
    
    def _generate_clarification_message(
        self,
        new_event: EventCache,
        conflict_event: EventCache
    ) -> str:
        """Generate message when user's intent is unclear"""
        
        return f"""I didn't understand your choice. Please reply with one of these options:

**Option 1:** "Keep new" → Keep **{new_event.summary}**
📅 {new_event.start_time.strftime('%B %d at %I:%M %p')}

**Option 2:** "Keep existing" → Keep **{conflict_event.summary}**  
📅 {conflict_event.start_time.strftime('%B %d at %I:%M %p')}

**Option 3:** "Cancel" → Cancel both events"""
    
    async def notify_conflict_async(
        self,
        user: User,
        new_event: EventCache,
        conflict_event: EventCache,
        llm_suggestion: str
    ) -> bool:
        """
        Send conflict notification to user via WhatsApp
        
        Args:
            user: User object
            new_event: New tentative event
            conflict_event: Existing conflicting event
            llm_suggestion: LLM's suggestion
            
        Returns:
            True if notification sent successfully
        """
        try:
            message = self.conflict_service.generate_conflict_message(
                new_event=new_event,
                conflict_event=conflict_event,
                llm_suggestion=llm_suggestion
            )
            
            success = await whatsapp_service.send_text_message(
                to=user.wa_phone,
                message=message
            )
            
            if success:
                logger.info(f"📤 Sent conflict notification to {user.wa_phone}")
            else:
                logger.error(f"Failed to send conflict notification to {user.wa_phone}")
            
            return success
            
        except Exception as e:
            logger.error(f"Error sending conflict notification: {e}")
            return False


# Global instance
decision_resolver = DecisionResolverService()
