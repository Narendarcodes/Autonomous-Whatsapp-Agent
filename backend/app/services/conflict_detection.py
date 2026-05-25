"""
Event Conflict Detection Service
Handles time overlap detection and multi-turn conflict resolution
"""

from typing import List, Optional, Tuple, Dict, Any
from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_
from uuid import UUID

from app.models.user import EventCache, PendingDecision, EventStatus, DecisionState
from app.core.logging import logger


class ConflictDetectionService:
    """Service for detecting and managing event conflicts"""
    
    @staticmethod
    async def check_time_overlap(
        db: AsyncSession,
        user_id: UUID,
        start_time: datetime,
        end_time: datetime,
        exclude_event_id: Optional[UUID] = None
    ) -> List[EventCache]:
        """
        Check if time range overlaps with existing events
        
        Args:
            db: Database session
            user_id: User UUID
            start_time: New event start time
            end_time: New event end time
            exclude_event_id: Event ID to exclude (for updates)
            
        Returns:
            List of conflicting EventCache objects
        """
        try:
            # Two events overlap if:
            # (StartA < EndB) AND (EndA > StartB)
            query = select(EventCache).where(
                and_(
                    EventCache.user_id == user_id,
                    EventCache.status.in_(['confirmed', 'tentative']),  # Use string values for PostgreSQL ENUM
                    or_(
                        # New event starts during existing event
                        and_(
                            EventCache.start_time <= start_time,
                            EventCache.end_time > start_time
                        ),
                        # New event ends during existing event
                        and_(
                            EventCache.start_time < end_time,
                            EventCache.end_time >= end_time
                        ),
                        # New event completely contains existing event
                        and_(
                            EventCache.start_time >= start_time,
                            EventCache.end_time <= end_time
                        )
                    )
                )
            )
            
            if exclude_event_id:
                query = query.where(EventCache.id != exclude_event_id)
            
            result = await db.execute(query)
            conflicts = result.scalars().all()
            
            if conflicts:
                logger.warning(f"⚠️ Found {len(conflicts)} time conflict(s) for user {user_id}")
            
            return list(conflicts)
            
        except Exception as e:
            logger.error(f"Error checking time overlap: {e}")
            return []
    
    @staticmethod
    async def create_pending_decision(
        db: AsyncSession,
        user_id: UUID,
        new_event: EventCache,
        conflict_event: EventCache,
        llm_suggestion: str,
        user_message: str
    ) -> Optional[PendingDecision]:
        """
        Create pending decision for conflict resolution
        
        Args:
            db: Database session
            user_id: User UUID
            new_event: Newly created tentative event
            conflict_event: Existing conflicting event
            llm_suggestion: LLM's suggested resolution
            user_message: Original user message that created conflict
            
        Returns:
            PendingDecision object or None if failed
        """
        try:
            pending_decision = PendingDecision(
                user_id=user_id,
                event_id=new_event.id,
                conflict_event_id=conflict_event.id,
                llm_suggestion=llm_suggestion,
                user_message=user_message,
                state='waiting_for_user'  # Use string value for PostgreSQL ENUM
            )
            
            db.add(pending_decision)
            await db.commit()
            await db.refresh(pending_decision)
            
            logger.info(f"📝 Created pending decision {pending_decision.id} for user {user_id}")
            
            return pending_decision
            
        except Exception as e:
            logger.error(f"Failed to create pending decision: {e}")
            await db.rollback()
            return None
    
    @staticmethod
    async def get_active_pending_decision(
        db: AsyncSession,
        user_id: UUID
    ) -> Optional[PendingDecision]:
        """
        Get active pending decision for user (if any)
        
        Args:
            db: Database session
            user_id: User UUID
            
        Returns:
            PendingDecision object or None
        """
        try:
            query = select(PendingDecision).where(
                and_(
                    PendingDecision.user_id == user_id,
                    PendingDecision.state == 'waiting_for_user'  # Use string value for PostgreSQL ENUM
                )
            ).order_by(PendingDecision.created_at.desc())
            
            result = await db.execute(query)
            pending = result.scalars().first()
            
            return pending
            
        except Exception as e:
            logger.error(f"Error getting pending decision: {e}")
            return None
    
    @staticmethod
    async def resolve_conflict(
        db: AsyncSession,
        pending_decision: PendingDecision,
        keep_new_event: bool
    ) -> bool:
        """
        Resolve conflict by keeping one event and cancelling the other
        
        Args:
            db: Database session
            pending_decision: PendingDecision to resolve
            keep_new_event: True to keep new event, False to keep existing
            
        Returns:
            True if resolved successfully
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
                return False
            
            if keep_new_event:
                # Confirm new event, cancel existing
                new_event.status = 'confirmed'  # Use string value for PostgreSQL ENUM
                conflict_event.status = 'cancelled'  # Use string value for PostgreSQL ENUM
                logger.info(f"✅ Kept new event {new_event.id}, cancelled {conflict_event.id}")
            else:
                # Cancel new event, keep existing
                new_event.status = 'cancelled'  # Use string value for PostgreSQL ENUM
                conflict_event.status = 'confirmed'  # Use string value for PostgreSQL ENUM
                logger.info(f"✅ Kept existing event {conflict_event.id}, cancelled {new_event.id}")
            
            # Mark decision as resolved
            pending_decision.state = 'resolved'  # Use string value for PostgreSQL ENUM
            pending_decision.resolved_at = datetime.utcnow()
            
            await db.commit()
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to resolve conflict: {e}")
            await db.rollback()
            return False
    
    @staticmethod
    async def cancel_pending_decision(
        db: AsyncSession,
        pending_decision: PendingDecision
    ) -> bool:
        """
        Cancel pending decision (both events remain tentative)
        
        Args:
            db: Database session
            pending_decision: PendingDecision to cancel
            
        Returns:
            True if cancelled successfully
        """
        try:
            pending_decision.state = 'cancelled'  # Use string value for PostgreSQL ENUM
            pending_decision.resolved_at = datetime.utcnow()
            
            await db.commit()
            
            logger.info(f"❌ Cancelled pending decision {pending_decision.id}")
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to cancel pending decision: {e}")
            await db.rollback()
            return False
    
    @staticmethod
    def generate_conflict_message(
        new_event: EventCache,
        conflict_event: EventCache,
        llm_suggestion: str
    ) -> str:
        """
        Generate user-facing message explaining the conflict
        
        Args:
            new_event: New tentative event
            conflict_event: Existing conflicting event
            llm_suggestion: LLM's suggestion
            
        Returns:
            Formatted conflict message
        """
        new_time = new_event.start_time.strftime("%B %d at %I:%M %p")
        conflict_time = conflict_event.start_time.strftime("%B %d at %I:%M %p")
        
        message = f"""⚠️ **Event Conflict Detected**

You have a time conflict:

**New Event:** {new_event.summary}
📅 {new_time}

**Conflicts with:** {conflict_event.summary}
📅 {conflict_time}

{llm_suggestion}

Which event would you like to keep?
Reply with:
• "Keep new" or "1" - Keep **{new_event.summary}**
• "Keep existing" or "2" - Keep **{conflict_event.summary}**
• "Cancel" - Cancel both events"""
        
        return message
    
    @staticmethod
    async def get_conflict_detection_candidates(
        db: AsyncSession,
        user_id: UUID,
        hours_ahead: int = 24
    ) -> List[Tuple[EventCache, EventCache]]:
        """
        Get pairs of events that might conflict in next N hours
        Used for proactive conflict detection job
        
        Args:
            db: Database session
            user_id: User UUID
            hours_ahead: How many hours to look ahead
            
        Returns:
            List of (event1, event2) tuples with potential conflicts
        """
        try:
            from datetime import timedelta
            
            start_time = datetime.utcnow()
            end_time = start_time + timedelta(hours=hours_ahead)
            
            # Get all confirmed/tentative events in time range
            query = select(EventCache).where(
                and_(
                    EventCache.user_id == user_id,
                    EventCache.status.in_(['confirmed', 'tentative']),  # Use string values for PostgreSQL ENUM
                    EventCache.start_time >= start_time,
                    EventCache.start_time <= end_time
                )
            ).order_by(EventCache.start_time)
            
            result = await db.execute(query)
            events = result.scalars().all()
            
            # Check each pair for overlap
            conflicts = []
            for i in range(len(events)):
                for j in range(i + 1, len(events)):
                    event1 = events[i]
                    event2 = events[j]
                    
                    # Check overlap
                    if event1.end_time > event2.start_time:
                        conflicts.append((event1, event2))
            
            if conflicts:
                logger.info(f"🔍 Found {len(conflicts)} potential conflict(s) for user {user_id}")
            
            return conflicts
            
        except Exception as e:
            logger.error(f"Error detecting conflicts: {e}")
            return []
