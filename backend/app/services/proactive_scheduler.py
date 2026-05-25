"""
Proactive Notification Scheduler
Schedules reminders, morning briefings, evening summaries, conflict detection
"""

from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta
from uuid import uuid4
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_

from app.models.user import EventCache, Reminder, User, EventStatus
from app.infrastructure.delayed_scheduler import DelayedJobScheduler
from app.core.logging import logger


class ProactiveScheduler:
    """Service for scheduling proactive notifications"""

    def __init__(self, scheduler: DelayedJobScheduler):
        """
        Initialize scheduler
        Args:
            scheduler: DelayedJobScheduler instance
        """
        self.scheduler = scheduler

    async def expand_future_recurring_instances(
        self,
        db: AsyncSession,
        master_event_id: str,
        user_id: str,
        look_ahead_days: int = 30
    ) -> int:
        """
        Expand future instances for a master recurring event and schedule reminders.
        Args:
            db: Database session
            master_event_id: Master recurring event ID
            user_id: User UUID as string
            look_ahead_days: How far ahead to expand instances
        Returns:
            Number of new instances created
        """
        try:
            from app.services.calendar_service_recurring import expand_recurring_event_instances
            # Expand instances
            new_instance_ids = await expand_recurring_event_instances(
                db=db,
                master_event_id=master_event_id,
                look_ahead_days=look_ahead_days
            )
            scheduled = 0
            for instance_id in new_instance_ids:
                from sqlalchemy import select
                from app.models.user import EventCache
                query = select(EventCache).where(EventCache.event_id == instance_id)
                res = await db.execute(query)
                event_cache = res.scalar_one_or_none()
                if event_cache:
                    await self.schedule_event_reminders(
                        db=db,
                        user_id=user_id,
                        event=event_cache
                    )
                    scheduled += 1
            logger.info(f"✅ Expanded {len(new_instance_ids)} future instances and scheduled {scheduled} reminders for master event {master_event_id}")
            return scheduled
        except Exception as e:
            logger.error(f"Error expanding recurring instances: {e}")
            return 0
    
    async def schedule_event_reminders(
        self,
        db: AsyncSession,
        user_id: str,
        event: EventCache
    ) -> List[str]:
        """
        Schedule all reminders for an event
        
        Args:
            db: Database session
            user_id: User UUID as string
            event: EventCache object
            
        Returns:
            List of scheduled job IDs
        """
        try:
            job_ids = []
            
            # Reminder types with their time offsets
            reminders = [
                ("15min", timedelta(minutes=15)),
                ("1hour", timedelta(hours=1)),
                ("1day", timedelta(days=1))
            ]
            
            for reminder_type, offset in reminders:
                scheduled_time = event.start_time - offset
                
                # Don't schedule if reminder is in the past
                if scheduled_time <= datetime.utcnow():
                    logger.debug(f"Skipping {reminder_type} reminder (in the past)")
                    continue
                
                job_id = f"reminder_{event.id}_{reminder_type}_{uuid4().hex[:8]}"
                
                payload = {
                    "event_id": str(event.id),
                    "event_summary": event.summary,
                    "event_start_time": event.start_time.isoformat(),
                    "reminder_type": reminder_type
                }
                
                success = await self.scheduler.schedule_job(
                    job_id=job_id,
                    job_type="event_reminder",
                    user_id=user_id,
                    payload=payload,
                    scheduled_time=scheduled_time
                )
                
                if success:
                    # Save reminder to database
                    reminder = Reminder(
                        user_id=event.user_id,
                        event_id=event.id,
                        reminder_type=reminder_type,
                        scheduled_time=scheduled_time,
                        redis_job_id=job_id,
                        sent=False
                    )
                    db.add(reminder)
                    job_ids.append(job_id)
                    
                    logger.info(f"📅 Scheduled {reminder_type} reminder for event {event.id}")
            
            await db.commit()
            
            return job_ids
            
        except Exception as e:
            logger.error(f"Error scheduling event reminders: {e}")
            await db.rollback()
            return []
    
    async def cancel_event_reminders(
        self,
        db: AsyncSession,
        event_id: str
    ) -> int:
        """
        Cancel all reminders for an event
        
        Args:
            db: Database session
            event_id: Event UUID as string
            
        Returns:
            Number of reminders cancelled
        """
        try:
            # Cancel jobs in Redis
            cancelled_count = await self.scheduler.cancel_jobs_for_event(event_id)
            
            # Mark reminders as cancelled in database
            query = select(Reminder).where(
                and_(
                    Reminder.event_id == event_id,
                    Reminder.sent == False
                )
            )
            result = await db.execute(query)
            reminders = result.scalars().all()
            
            for reminder in reminders:
                reminder.sent = True  # Mark as sent to prevent re-scheduling
            
            await db.commit()
            
            logger.info(f"❌ Cancelled {cancelled_count} reminder(s) for event {event_id}")
            
            return cancelled_count
            
        except Exception as e:
            logger.error(f"Error cancelling reminders: {e}")
            await db.rollback()
            return 0
    
    async def schedule_morning_briefing(
        self,
        user_id: str,
        target_time: datetime
    ) -> Optional[str]:
        """
        Schedule morning briefing (8 AM daily)
        
        Args:
            user_id: User UUID as string
            target_time: Target time for briefing (8 AM)
            
        Returns:
            Job ID or None if failed
        """
        try:
            job_id = f"morning_briefing_{user_id}_{target_time.date()}_{uuid4().hex[:8]}"
            
            payload = {
                "briefing_date": target_time.date().isoformat()
            }
            
            success = await self.scheduler.schedule_job(
                job_id=job_id,
                job_type="morning_briefing",
                user_id=user_id,
                payload=payload,
                scheduled_time=target_time
            )
            
            if success:
                logger.info(f"🌅 Scheduled morning briefing for user {user_id} at {target_time}")
                return job_id
            
            return None
            
        except Exception as e:
            logger.error(f"Error scheduling morning briefing: {e}")
            return None
    
    async def schedule_evening_summary(
        self,
        user_id: str,
        target_time: datetime
    ) -> Optional[str]:
        """
        Schedule evening summary (8 PM daily)
        
        Args:
            user_id: User UUID as string
            target_time: Target time for summary (8 PM)
            
        Returns:
            Job ID or None if failed
        """
        try:
            job_id = f"evening_summary_{user_id}_{target_time.date()}_{uuid4().hex[:8]}"
            
            payload = {
                "summary_date": target_time.date().isoformat()
            }
            
            success = await self.scheduler.schedule_job(
                job_id=job_id,
                job_type="evening_summary",
                user_id=user_id,
                payload=payload,
                scheduled_time=target_time
            )
            
            if success:
                logger.info(f"🌆 Scheduled evening summary for user {user_id} at {target_time}")
                return job_id
            
            return None
            
        except Exception as e:
            logger.error(f"Error scheduling evening summary: {e}")
            return None
    
    async def schedule_conflict_detection(
        self,
        user_id: str,
        target_time: datetime
    ) -> Optional[str]:
        """
        Schedule conflict detection job (every 30 min)
        
        Args:
            user_id: User UUID as string
            target_time: Target time for conflict check
            
        Returns:
            Job ID or None if failed
        """
        try:
            job_id = f"conflict_check_{user_id}_{target_time.timestamp()}_{uuid4().hex[:8]}"
            
            payload = {
                "check_hours_ahead": 24  # Look 24 hours ahead
            }
            
            success = await self.scheduler.schedule_job(
                job_id=job_id,
                job_type="conflict_detection",
                user_id=user_id,
                payload=payload,
                scheduled_time=target_time
            )
            
            if success:
                logger.info(f"🔍 Scheduled conflict detection for user {user_id} at {target_time}")
                return job_id
            
            return None
            
        except Exception as e:
            logger.error(f"Error scheduling conflict detection: {e}")
            return None
    
    async def schedule_weekly_insights(
        self,
        user_id: str,
        target_time: datetime
    ) -> Optional[str]:
        """
        Schedule weekly insights (Monday 9 AM)
        
        Args:
            user_id: User UUID as string
            target_time: Target time for insights (Monday 9 AM)
            
        Returns:
            Job ID or None if failed
        """
        try:
            job_id = f"weekly_insights_{user_id}_{target_time.date()}_{uuid4().hex[:8]}"
            
            payload = {
                "week_start": target_time.date().isoformat()
            }
            
            success = await self.scheduler.schedule_job(
                job_id=job_id,
                job_type="weekly_insights",
                user_id=user_id,
                payload=payload,
                scheduled_time=target_time
            )
            
            if success:
                logger.info(f"📊 Scheduled weekly insights for user {user_id} at {target_time}")
                return job_id
            
            return None
            
        except Exception as e:
            logger.error(f"Error scheduling weekly insights: {e}")
            return None
    
    async def bootstrap_user_schedule(
        self,
        db: AsyncSession,
        user_id: str
    ) -> Dict[str, List[str]]:
        """
        Bootstrap all recurring jobs for a new user
        
        Args:
            db: Database session
            user_id: User UUID as string
            
        Returns:
            Dictionary of job types and their job IDs
        """
        try:
            now = datetime.utcnow()
            scheduled_jobs = {
                "morning_briefings": [],
                "evening_summaries": [],
                "conflict_checks": [],
                "weekly_insights": []
            }
            
            # Schedule next 7 days of morning briefings (8 AM)
            for day in range(7):
                target_date = now + timedelta(days=day)
                target_time = target_date.replace(hour=8, minute=0, second=0, microsecond=0)
                
                if target_time > now:
                    job_id = await self.schedule_morning_briefing(user_id, target_time)
                    if job_id:
                        scheduled_jobs["morning_briefings"].append(job_id)
            
            # Schedule next 7 days of evening summaries (8 PM)
            for day in range(7):
                target_date = now + timedelta(days=day)
                target_time = target_date.replace(hour=20, minute=0, second=0, microsecond=0)
                
                if target_time > now:
                    job_id = await self.schedule_evening_summary(user_id, target_time)
                    if job_id:
                        scheduled_jobs["evening_summaries"].append(job_id)
            
            # Schedule conflict checks every 30 min for next 24 hours
            for interval in range(48):  # 48 * 30min = 24 hours
                target_time = now + timedelta(minutes=30 * interval)
                job_id = await self.schedule_conflict_detection(user_id, target_time)
                if job_id:
                    scheduled_jobs["conflict_checks"].append(job_id)
            
            # Schedule next Monday's weekly insights (9 AM)
            days_until_monday = (7 - now.weekday()) % 7
            if days_until_monday == 0:
                days_until_monday = 7  # Next Monday, not today
            
            next_monday = now + timedelta(days=days_until_monday)
            target_time = next_monday.replace(hour=9, minute=0, second=0, microsecond=0)
            
            job_id = await self.schedule_weekly_insights(user_id, target_time)
            if job_id:
                scheduled_jobs["weekly_insights"].append(job_id)
            
            logger.info(f"✅ Bootstrapped schedule for user {user_id}")
            
            return scheduled_jobs
            
        except Exception as e:
            logger.error(f"Error bootstrapping user schedule: {e}")
            return {}
