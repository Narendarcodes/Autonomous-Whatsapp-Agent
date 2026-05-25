"""
Scheduler Worker
Polls Redis Sorted Set for due jobs and delivers them to scheduled_jobs_stream
"""

import asyncio
import signal
import sys
import json
from typing import Dict, Any, Tuple
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from sqlalchemy import select

from app.core.config import settings
from app.core.logging import logger
from app.db.database import get_async_session
from app.db.redis_client import redis_client
from app.infrastructure.delayed_scheduler import DelayedJobScheduler
from app.infrastructure.redis_streams import RedisStreamProducer
from app.models.user import User, EventCache, Reminder
from app.services.whatsapp_service import whatsapp_service
from app.services.calendar_service import calendar_service
from app.services.conflict_detection import ConflictDetectionService


def convert_to_user_timezone(dt: datetime, user_timezone: str) -> datetime:
    """
    Convert a datetime to user's timezone
    
    Args:
        dt: datetime (assumed UTC if no timezone)
        user_timezone: User's timezone string (e.g., "Asia/Kolkata")
    
    Returns:
        datetime in user's timezone
    """
    try:
        # If dt is naive (no timezone), assume it's UTC
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=ZoneInfo("UTC"))
        
        # Convert to user's timezone
        user_tz = ZoneInfo(user_timezone)
        return dt.astimezone(user_tz)
    except Exception as e:
        logger.warning(f"Failed to convert timezone: {e}, using original time")
        return dt


def is_oauth_error(error: Exception) -> bool:
    """Check if an error is related to OAuth token expiration/revocation"""
    error_str = str(error).lower()
    oauth_keywords = [
        'invalid_grant',
        'token has been expired',
        'token has been revoked',
        'invalid credentials',
        'unauthorized',
        'refresh token',
        'access token'
    ]
    return any(keyword in error_str for keyword in oauth_keywords)


class SchedulerWorker:
    """Worker that polls delayed jobs and executes them"""
    
    def __init__(self):
        self.running = False
        self.scheduler: DelayedJobScheduler = None
        self.producer: RedisStreamProducer = None
        self.conflict_service = ConflictDetectionService()
    
    async def start(self):
        """Start the scheduler worker"""
        try:
            logger.info("🚀 Starting Scheduler Worker...")
            
            # Connect to Redis
            await redis_client.connect()

            # Initialize scheduler and producer
            self.scheduler = DelayedJobScheduler(redis_client)
            self.producer = RedisStreamProducer(redis_client)
            
            # Setup signal handlers
            signal.signal(signal.SIGINT, self._handle_shutdown)
            signal.signal(signal.SIGTERM, self._handle_shutdown)
            
            self.running = True
            logger.info("✅ Scheduler Worker started successfully")
            
            # Main polling loop
            await self._poll_loop()
            
        except Exception as e:
            logger.error(f"Failed to start Scheduler Worker: {e}")
            sys.exit(1)
    
    async def _poll_loop(self):
        """Main polling loop (checks every second)"""
        
        while self.running:
            try:
                # Get due jobs
                due_jobs = await self.scheduler.get_due_jobs(limit=100)
                
                if due_jobs:
                    logger.info(f"⏰ Found {len(due_jobs)} due job(s)")
                    
                    for job in due_jobs:
                        await self._execute_job(job)
                
                # Cleanup stuck jobs every 5 minutes
                if datetime.utcnow().minute % 5 == 0 and datetime.utcnow().second < 2:
                    cleaned = await self.scheduler.cleanup_stuck_jobs(timeout_seconds=300)
                    if cleaned > 0:
                        logger.info(f"🔧 Cleaned up {cleaned} stuck job(s)")
                
                # Expand recurring events daily at 2 AM
                now = datetime.utcnow()
                if now.hour == 2 and now.minute == 0 and now.second < 2:
                    # Use a separate task to not block polling
                    asyncio.create_task(self._run_daily_expansion())
                
                # Sleep for 1 second
                await asyncio.sleep(1)
                
            except Exception as e:
                logger.error(f"Error in poll loop: {e}")
                await asyncio.sleep(5)
    
    async def _execute_job(self, job: Dict[str, Any]):
        """
        Execute a single job
        
        Args:
            job: Job dictionary from Redis Sorted Set
        """
        try:
            job_id = job.get("job_id")
            job_type = job.get("job_type")
            user_id = job.get("user_id")
            payload = job.get("payload", {})
            
            # Mark as processing (prevents duplicate execution)
            job_json = json.dumps({k: v for k, v in job.items() if k != 'score'})
            
            if not await self.scheduler.mark_job_processing(job_id, job_json):
                logger.warning(f"Job {job_id} already being processed")
                return
            
            logger.info(f"🔄 Executing job: {job_type} (id={job_id})")
            
            # Route to appropriate handler
            if job_type == "event_reminder":
                success = await self._handle_event_reminder(user_id, payload)
            elif job_type == "morning_briefing":
                success = await self._handle_morning_briefing(user_id, payload)
            elif job_type == "evening_summary":
                success = await self._handle_evening_summary(user_id, payload)
            elif job_type == "conflict_detection":
                success = await self._handle_conflict_detection(user_id, payload)
            elif job_type == "weekly_insights":
                success = await self._handle_weekly_insights(user_id, payload)
            else:
                logger.warning(f"Unknown job type: {job_type}")
                success = False
            
            if success:
                # Mark as completed
                await self.scheduler.complete_job(job_json)
                logger.info(f"✅ Job {job_id} completed successfully")
            else:
                # Reschedule for retry (1 minute later)
                retry_time = datetime.utcnow() + timedelta(minutes=1)
                await self.scheduler.reschedule_job(job_json, retry_time)
                logger.warning(f"🔄 Job {job_id} rescheduled for retry")
            
        except Exception as e:
            logger.error(f"Error executing job: {e}")
    
    async def _handle_event_reminder(self, user_id: str, payload: Dict[str, Any]) -> bool:
        """Handle event reminder notification"""
        try:
            event_id = payload.get("event_id")
            event_summary = payload.get("event_summary")
            event_start_time = payload.get("event_start_time")
            reminder_type = payload.get("reminder_type")
            
            # Get database session
            async for db in get_async_session():
                try:
                    # Load user
                    query = select(User).where(User.id == user_id)
                    result = await db.execute(query)
                    user = result.scalar_one_or_none()
                    
                    if not user:
                        logger.error(f"User {user_id} not found")
                        return False
                    
                    # Format friendly reminder message
                    if reminder_type == "15min":
                        time_text = "in 15 minutes"
                        emoji = "⏰"
                        encouragement = "Almost time! You've got this! 💪"
                    elif reminder_type == "1hour":
                        time_text = "in 1 hour"
                        emoji = "🔔"
                        encouragement = "Heads up so you can prepare! ✨"
                    elif reminder_type == "1day":
                        time_text = "tomorrow"
                        emoji = "📅"
                        encouragement = "Just wanted to give you a heads up! 😊"
                    else:
                        time_text = "coming up"
                        emoji = "⏰"
                        encouragement = "Here's a friendly reminder! 🌟"
                    
                    # Parse event time and convert to user's timezone
                    start_dt = datetime.fromisoformat(event_start_time)
                    user_timezone = user.timezone or "UTC"
                    local_dt = convert_to_user_timezone(start_dt, user_timezone)
                    formatted_time = local_dt.strftime("%I:%M %p")
                    
                    message = f"{emoji} **Friendly Reminder**\n\n📌 {event_summary}\n🕐 {formatted_time} ({time_text})\n\n{encouragement}"
                    
                    # Send via WhatsApp
                    success = await whatsapp_service.send_text_message(
                        to=user.wa_phone,
                        message=message
                    )
                    
                    if success:
                        # Mark reminder as sent in database (skip for test events)
                        if event_id and not event_id.startswith("test"):
                            try:
                                from uuid import UUID
                                event_uuid = UUID(event_id)
                                reminder_query = select(Reminder).where(
                                    Reminder.event_id == event_uuid,
                                    Reminder.reminder_type == reminder_type,
                                    Reminder.sent == False
                                )
                                reminder_result = await db.execute(reminder_query)
                                reminder = reminder_result.scalar_one_or_none()
                                
                                if reminder:
                                    reminder.sent = True
                                    reminder.sent_at = datetime.utcnow()
                                    await db.commit()
                            except (ValueError, TypeError):
                                # Invalid UUID, skip database update
                                pass
                    
                    return success
                    
                finally:
                    await db.close()
        
        except Exception as e:
            logger.error(f"Error handling event reminder: {e}")
            return False
    
    async def _handle_morning_briefing(self, user_id: str, payload: Dict[str, Any]) -> bool:
        """Handle morning briefing notification"""
        try:
            async for db in get_async_session():
                try:
                    # Load user
                    query = select(User).where(User.id == user_id)
                    result = await db.execute(query)
                    user = result.scalar_one_or_none()
                    
                    if not user:
                        return False
                    
                    # Get user's timezone
                    user_timezone = user.timezone or "UTC"
                    user_tz = ZoneInfo(user_timezone)
                    
                    # Get today's events in user's timezone
                    now_local = datetime.now(user_tz)
                    today_start = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
                    today_end = today_start + timedelta(days=1)
                    
                    events = await calendar_service.list_events(
                        user=user,
                        db=db,
                        start_date=today_start,
                        end_date=today_end,
                        max_results=10
                    )
                    
                    if not events:
                        message = "🌅 **Good Morning!** ☀️\n\nYour schedule is wide open today! 🎉\n\nThis is a great opportunity to:\n• Focus on that project you've been thinking about\n• Take some time for yourself\n• Or just enjoy a relaxed day!\n\nHow are you feeling today? I'm here if you need anything! 💪"
                    else:
                        event_list = []
                        for event in events[:5]:  # Limit to 5 events
                            summary = event.get("summary", "Untitled")
                            start = event.get("start", {})
                            
                            if "dateTime" in start:
                                start_dt = datetime.fromisoformat(start["dateTime"].replace('Z', '+00:00'))
                                local_dt = convert_to_user_timezone(start_dt, user_timezone)
                                time_str = local_dt.strftime('%I:%M %p')
                            else:
                                time_str = "All day"
                            
                            event_list.append(f"• {summary} - {time_str}")
                        
                        event_text = "\n".join(event_list)
                        event_count = len(events[:5])
                        
                        if event_count >= 4:
                            closing = "\n\nLooks like a busy day ahead! 💼 Remember to take breaks and stay hydrated. You've got this! 💪"
                        elif event_count >= 2:
                            closing = "\n\nA productive day awaits! ✨ Let me know if you need any changes to your schedule."
                        else:
                            closing = "\n\nA nice balanced day! 🌟 Plenty of time to focus on what matters to you."
                        
                        message = f"🌅 **Good Morning!** ☀️\n\nHere's what's on your plate today:\n\n{event_text}{closing}"
                    
                    # Send via WhatsApp
                    success = await whatsapp_service.send_text_message(
                        to=user.wa_phone,
                        message=message
                    )
                    
                    return success
                    
                finally:
                    await db.close()
        
        except Exception as e:
            logger.error(f"Error handling morning briefing: {e}")
            # Check if it's an OAuth error - notify user instead of retrying
            if is_oauth_error(e):
                try:
                    async for db in get_async_session():
                        try:
                            query = select(User).where(User.id == user_id)
                            result = await db.execute(query)
                            user = result.scalar_one_or_none()
                            if user:
                                # Use BASE_URL - should be set to ngrok/public URL in production
                                base_url = settings.BASE_URL
                                # Don't send localhost URLs - they won't work on phone
                                if "localhost" in base_url or "127.0.0.1" in base_url:
                                    await whatsapp_service.send_text_message(
                                        to=user.wa_phone,
                                        message=f"🔐 **Google Calendar Reconnection Needed**\n\nHey! It looks like I've lost access to your Google Calendar. This can happen when tokens expire.\n\nPlease reconnect by visiting:\n👉 YOUR_NGROK_URL/oauth/login?phone={user.wa_phone}\n\n(Replace YOUR_NGROK_URL with your actual ngrok URL)\n\nOnce you reconnect, I'll resume your briefings and reminders! 😊"
                                    )
                                else:
                                    oauth_url = f"{base_url}/oauth/login?phone={user.wa_phone}"
                                    await whatsapp_service.send_text_message(
                                        to=user.wa_phone,
                                        message=f"🔐 **Google Calendar Reconnection Needed**\n\nHey! It looks like I've lost access to your Google Calendar. This can happen when tokens expire.\n\nPlease reconnect so I can continue helping you:\n👉 {oauth_url}\n\nOnce you reconnect, I'll be able to send you briefings and reminders again! 😊"
                                    )
                                logger.info(f"Sent OAuth reconnection request to {user.wa_phone}")
                        finally:
                            await db.close()
                except Exception as notify_error:
                    logger.error(f"Failed to send OAuth notification: {notify_error}")
                # Return True to prevent infinite retries
                return True
            return False
    
    async def _handle_evening_summary(self, user_id: str, payload: Dict[str, Any]) -> bool:
        """Handle evening summary notification"""
        try:
            async for db in get_async_session():
                try:
                    # Load user
                    query = select(User).where(User.id == user_id)
                    result = await db.execute(query)
                    user = result.scalar_one_or_none()
                    
                    if not user:
                        return False
                    
                    # Get user's timezone
                    user_timezone = user.timezone or "UTC"
                    user_tz = ZoneInfo(user_timezone)
                    
                    # Get tomorrow's events in user's timezone
                    now_local = datetime.now(user_tz)
                    tomorrow_start = now_local.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
                    tomorrow_end = tomorrow_start + timedelta(days=1)
                    
                    events = await calendar_service.list_events(
                        user=user,
                        db=db,
                        start_date=tomorrow_start,
                        end_date=tomorrow_end,
                        max_results=10
                    )
                    
                    if not events:
                        message = "🌙 **Good Evening!** ✨\n\nTomorrow's looking clear! 🎉\n\nNo scheduled events - a perfect opportunity to:\n• Catch up on personal projects\n• Spend quality time with loved ones\n• Or simply recharge\n\nHope you had a great day! Rest well tonight 😴💤"
                    else:
                        event_list = []
                        for event in events[:5]:
                            summary = event.get("summary", "Untitled")
                            start = event.get("start", {})
                            
                            if "dateTime" in start:
                                start_dt = datetime.fromisoformat(start["dateTime"].replace('Z', '+00:00'))
                                local_dt = convert_to_user_timezone(start_dt, user_timezone)
                                time_str = local_dt.strftime('%I:%M %p')
                            else:
                                time_str = "All day"
                            
                            event_list.append(f"• {summary} - {time_str}")
                        
                        event_text = "\n".join(event_list)
                        event_count = len(events[:5])
                        
                        if event_count >= 4:
                            closing = "\n\nBusy day tomorrow! Get some good rest tonight - you'll crush it! 💪😊"
                        elif event_count >= 2:
                            closing = "\n\nA productive day ahead! Sleep well and I'll be here in the morning to help. 🌟"
                        else:
                            closing = "\n\nA manageable day ahead! Enjoy your evening and rest up. 😊"
                        
                        message = f"🌙 **Good Evening!** ✨\n\nHere's a heads up on tomorrow:\n\n{event_text}{closing}"
                    
                    success = await whatsapp_service.send_text_message(
                        to=user.wa_phone,
                        message=message
                    )
                    
                    return success
                    
                finally:
                    await db.close()
        
        except Exception as e:
            logger.error(f"Error handling evening summary: {e}")
            # Check if it's an OAuth error - notify user instead of retrying
            if is_oauth_error(e):
                try:
                    async for db in get_async_session():
                        try:
                            query = select(User).where(User.id == user_id)
                            result = await db.execute(query)
                            user = result.scalar_one_or_none()
                            if user:
                                # Use BASE_URL - should be set to ngrok/public URL in production
                                base_url = settings.BASE_URL
                                # Don't send localhost URLs - they won't work on phone
                                if "localhost" in base_url or "127.0.0.1" in base_url:
                                    await whatsapp_service.send_text_message(
                                        to=user.wa_phone,
                                        message=f"🔐 **Google Calendar Reconnection Needed**\n\nHey! It looks like I've lost access to your Google Calendar.\n\nPlease reconnect by visiting:\n👉 YOUR_NGROK_URL/oauth/login?phone={user.wa_phone}\n\n(Replace YOUR_NGROK_URL with your actual ngrok URL)\n\nOnce reconnected, I'll resume your evening summaries! 😊"
                                    )
                                else:
                                    oauth_url = f"{base_url}/oauth/login?phone={user.wa_phone}"
                                    await whatsapp_service.send_text_message(
                                        to=user.wa_phone,
                                        message=f"🔐 **Google Calendar Reconnection Needed**\n\nHey! It looks like I've lost access to your Google Calendar.\n\nPlease reconnect so I can continue helping you:\n👉 {oauth_url}\n\nOnce reconnected, I'll resume your evening summaries! 😊"
                                    )
                                logger.info(f"Sent OAuth reconnection request to {user.wa_phone}")
                        finally:
                            await db.close()
                except Exception as notify_error:
                    logger.error(f"Failed to send OAuth notification: {notify_error}")
                # Return True to prevent infinite retries
                return True
            return False
    
    async def _handle_conflict_detection(self, user_id: str, payload: Dict[str, Any]) -> bool:
        """Handle proactive conflict detection"""
        try:
            async for db in get_async_session():
                try:
                    # Load user
                    query = select(User).where(User.id == user_id)
                    result = await db.execute(query)
                    user = result.scalar_one_or_none()
                    
                    if not user:
                        return False
                    
                    # Check for conflicts
                    hours_ahead = payload.get("check_hours_ahead", 24)
                    conflicts = await self.conflict_service.get_conflict_detection_candidates(
                        db=db,
                        user_id=user.id,
                        hours_ahead=hours_ahead
                    )
                    
                    if conflicts:
                        # Notify user of conflicts
                        conflict_messages = []
                        for event1, event2 in conflicts[:3]:  # Limit to 3 conflicts
                            time1 = event1.start_time.strftime("%b %d %I:%M %p")
                            time2 = event2.start_time.strftime("%b %d %I:%M %p")
                            conflict_messages.append(
                                f"• {event1.summary} ({time1}) overlaps with {event2.summary} ({time2})"
                            )
                        
                        message = f"⚠️ **Conflict Alert**\n\nWe detected {len(conflicts)} scheduling conflict(s):\n\n" + "\n".join(conflict_messages)
                        
                        success = await whatsapp_service.send_text_message(
                            to=user.wa_phone,
                            message=message
                        )
                        
                        return success
                    
                    return True  # No conflicts, job successful
                    
                finally:
                    await db.close()
        
        except Exception as e:
            logger.error(f"Error handling conflict detection: {e}")
            return False
    
    async def _handle_weekly_insights(self, user_id: str, payload: Dict[str, Any]) -> bool:
        """Handle weekly insights notification"""
        try:
            async for db in get_async_session():
                try:
                    # Load user
                    query = select(User).where(User.id == user_id)
                    result = await db.execute(query)
                    user = result.scalar_one_or_none()
                    
                    if not user:
                        return False
                    
                    # Get this week's events
                    week_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
                    week_end = week_start + timedelta(days=7)
                    
                    events = await calendar_service.list_events(
                        user=user,
                        db=db,
                        start_date=week_start,
                        end_date=week_end,
                        max_results=50
                    )
                    
                    event_count = len(events)
                    message = f"📊 **Weekly Insights**\n\nYou have {event_count} event(s) scheduled this week.\n\nStay organized and have a productive week!"
                    
                    success = await whatsapp_service.send_text_message(
                        to=user.wa_phone,
                        message=message
                    )
                    
                    return success
                    
                finally:
                    await db.close()
        
        except Exception as e:
            logger.error(f"Error handling weekly insights: {e}")
            return False
    
    async def _run_daily_expansion(self):
        """Wrapper to run expansion with DB session"""
        try:
            logger.info("🔄 Starting daily recurring event expansion...")
            async for db in get_async_session():
                await self._expand_recurring_events(db)
                break # Only need one session
        except Exception as e:
            logger.error(f"Error in daily expansion: {e}")

    async def _expand_recurring_events(self, db):
        """
        Expand recurring event instances
        Run daily to maintain 60-day horizon
        """
        try:
            from sqlalchemy import select
            from app.models.user import EventCache
            from app.services.recurrence_service import RecurrenceService
            from datetime import datetime, timedelta
            
            # Find master recurring events
            query = select(EventCache).where(
                EventCache.is_recurring == True,
                EventCache.recurring_event_id == None  # Masters only
            )
            result = await db.execute(query)
            master_events = result.scalars().all()
            
            recurrence_service = RecurrenceService()
            expanded_count = 0
            
            for master in master_events:
                # Check what's the last instance we have
                last_instance_query = select(EventCache).where(
                    EventCache.recurring_event_id == master.event_id
                ).order_by(EventCache.instance_date.desc()).limit(1)
                
                last_result = await db.execute(last_instance_query)
                last_instance = last_result.scalar_one_or_none()
                
                if last_instance:
                    # Generate from last instance + 1 day up to 60 days from now
                    start_expand = last_instance.instance_date + timedelta(days=1)
                else:
                    # No instances yet, start from master's start time
                    start_expand = master.start_time.date()
                
                # Generate instances
                instances = recurrence_service.expand_recurrence(
                    start_time=datetime.combine(start_expand, master.start_time.time()),
                    recurrence_rule=master.recurrence_rule,
                    horizon_days=60
                )
                
                duration = master.end_time - master.start_time
                
                for instance_start in instances:
                    # Skip if already exists
                    existing = await db.execute(
                        select(EventCache).where(
                            EventCache.recurring_event_id == master.event_id,
                            EventCache.instance_date == instance_start.date()
                        )
                    )
                    if existing.scalar_one_or_none():
                        continue
                    
                    instance_end = instance_start + duration
                    instance_id = f"{master.event_id}_{instance_start.date().isoformat().replace('-', '')}"
                    
                    new_instance = EventCache(
                        user_id=master.user_id,
                        event_id=instance_id,
                        summary=master.summary,
                        description=master.description,
                        location=master.location,
                        start_time=instance_start,
                        end_time=instance_end,
                        is_recurring=False,
                        recurring_event_id=master.event_id,
                        instance_date=instance_start.date(),
                        status=master.status
                    )
                    db.add(new_instance)
                    expanded_count += 1
                
            await db.commit()
            if expanded_count > 0:
                logger.info(f"✅ Expanded {expanded_count} recurring event instances")
            
        except Exception as e:
            logger.error(f"Failed to expand recurring events: {e}")
            await db.rollback()

    def _handle_shutdown(self, signum, frame):
        """Handle shutdown signals gracefully"""
        logger.info(f"🛑 Received shutdown signal ({signum})")
        self.running = False
    
    async def stop(self):
        """Stop the worker gracefully"""
        logger.info("🛑 Stopping Scheduler Worker...")
        self.running = False
        logger.info("✅ Scheduler Worker stopped")


async def main():
    """Main entry point for Scheduler Worker"""
    worker = SchedulerWorker()
    
    try:
        await worker.start()
    except KeyboardInterrupt:
        logger.info("🛑 Interrupted by user")
    finally:
        await worker.stop()


if __name__ == "__main__":
    asyncio.run(main())
