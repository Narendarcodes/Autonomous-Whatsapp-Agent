"""
Calendar Service - Recurring Event Support
"""
from typing import Dict, Any
from datetime import datetime, timedelta
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.user import EventCache, EventStatus, User
from app.services.recurrence_service import RecurrenceService
from app.core.logging import logger

def convert_recurrence_dict_to_rrule(recurrence: dict) -> str:
    """
    Convert recurrence dict from LLM to RRULE string.
    Example: {'frequency': 'WEEKLY', 'by_day': ['TU']} -> 'RRULE:FREQ=WEEKLY;BYDAY=TU'
    """
    if isinstance(recurrence, str):
        # Already a string, return as-is
        return recurrence if recurrence.startswith('RRULE:') else f'RRULE:{recurrence}'
    
    parts = []
    
    # Frequency (required)
    freq = recurrence.get('frequency', 'DAILY').upper()
    parts.append(f'FREQ={freq}')
    
    # Interval (optional)
    interval = recurrence.get('interval')
    if interval and interval > 1:
        parts.append(f'INTERVAL={interval}')
    
    # Count (optional)
    count = recurrence.get('count')
    if count:
        parts.append(f'COUNT={count}')
    
    # Until (optional)
    until = recurrence.get('until')
    if until:
        if isinstance(until, str):
            # Parse date string and format as RRULE date
            until_dt = datetime.fromisoformat(until.replace('Z', '+00:00'))
            parts.append(f'UNTIL={until_dt.strftime("%Y%m%dT%H%M%SZ")}')
    
    # By day (optional) - for weekly recurrence
    by_day = recurrence.get('by_day')
    if by_day:
        if isinstance(by_day, list):
            parts.append(f'BYDAY={",".join(by_day)}')
        else:
            parts.append(f'BYDAY={by_day}')
    
    # By month day (optional) - for monthly recurrence
    by_month_day = recurrence.get('by_month_day')
    if by_month_day:
        if isinstance(by_month_day, list):
            parts.append(f'BYMONTHDAY={",".join(map(str, by_month_day))}')
        else:
            parts.append(f'BYMONTHDAY={by_month_day}')
    
    return 'RRULE:' + ';'.join(parts)


def strip_timezone(dt: datetime) -> datetime:
    """Strip timezone info from datetime for naive storage"""
    if dt.tzinfo is not None:
        # Convert to UTC then strip timezone
        from datetime import timezone
        utc_dt = dt.astimezone(timezone.utc)
        return utc_dt.replace(tzinfo=None)
    return dt


class CalendarServiceRecurring:
    async def create_recurring_event(
        self,
        user: User,
        db: AsyncSession,
        event_data,
        recurrence_rule
    ) -> Dict[str, Any]:
        """
        Create recurring event with instance expansion
        recurrence_rule can be a dict or string
        """
        try:
            # Convert dict to RRULE string if needed
            if isinstance(recurrence_rule, dict):
                recurrence_rule = convert_recurrence_dict_to_rrule(recurrence_rule)
                logger.info(f"Converted recurrence dict to RRULE: {recurrence_rule}")
            
            # Strip timezone from datetimes
            start_time = strip_timezone(event_data.start_time)
            end_time = strip_timezone(event_data.end_time)
            
            # Simulate Google Calendar API call
            master_event_id = f"rec_{user.id}_{start_time.strftime('%Y%m%d%H%M')}"
            # Store master event
            master_cache = EventCache(
                user_id=user.id,
                google_event_id=master_event_id,
                summary=event_data.summary,
                description=event_data.description,
                location=event_data.location,
                start_time=start_time,
                end_time=end_time,
                is_recurring=True,
                recurrence_rule=recurrence_rule,
                recurring_event_id=None,
                instance_date=None,
                status='confirmed'  # Use string value for PostgreSQL ENUM
            )
            db.add(master_cache)
            # Expand and store instances (next 60 days)
            instances = RecurrenceService.expand_recurrence(
                start_time=start_time,
                recurrence_rule=recurrence_rule,
                horizon_days=60
            )
            duration = end_time - start_time
            for instance_start in instances[1:]:
                instance_end = instance_start + duration
                instance_id = f"{master_event_id}_{instance_start.date().isoformat().replace('-', '')}"
                instance_cache = EventCache(
                    user_id=user.id,
                    google_event_id=instance_id,
                    summary=event_data.summary,
                    description=event_data.description,
                    location=event_data.location,
                    start_time=instance_start,
                    end_time=instance_end,
                    is_recurring=False,
                    recurrence_rule=None,
                    recurring_event_id=master_event_id,
                    instance_date=instance_start.date(),
                    status='confirmed'  # Use string value for PostgreSQL ENUM
                )
                db.add(instance_cache)
            await db.commit()
            logger.info(f"✅ Created recurring event {master_event_id} with {len(instances)} instances")
            return {"id": master_event_id, "summary": event_data.summary}
        except Exception as e:
            await db.rollback()
            logger.error(f"Failed to create recurring event: {e}")
            raise

async def expand_recurring_event_instances(
    db: AsyncSession,
    master_event_id: str,
    look_ahead_days: int = 60
) -> list[str]:
    """
    Expand future instances for a master recurring event
    """
    from sqlalchemy import select
    
    try:
        # Get master event
        query = select(EventCache).where(EventCache.google_event_id == master_event_id)
        result = await db.execute(query)
        master_event = result.scalar_one_or_none()
        
        if not master_event or not master_event.is_recurring:
            return []
            
        # Expand instances
        instances = RecurrenceService.expand_recurrence(
            start_time=master_event.start_time,
            recurrence_rule=master_event.recurrence_rule,
            horizon_days=look_ahead_days
        )
        
        new_instance_ids = []
        duration = master_event.end_time - master_event.start_time
        
        for instance_start in instances:
            # Skip if same as master start time (master is the first instance)
            if instance_start == master_event.start_time:
                continue
                
            instance_id = f"{master_event_id}_{instance_start.date().isoformat().replace('-', '')}"
            
            # Check if exists
            check_query = select(EventCache).where(EventCache.google_event_id == instance_id)
            check_res = await db.execute(check_query)
            if check_res.scalar_one_or_none():
                continue
                
            instance_end = instance_start + duration
            
            instance_cache = EventCache(
                user_id=master_event.user_id,
                google_event_id=instance_id,
                summary=master_event.summary,
                description=master_event.description,
                location=master_event.location,
                start_time=instance_start,
                end_time=instance_end,
                is_recurring=False,
                recurrence_rule=None,
                recurring_event_id=master_event_id,
                instance_date=instance_start.date(),
                status=master_event.status
            )
            db.add(instance_cache)
            new_instance_ids.append(instance_id)
            
        await db.commit()
        return new_instance_ids
        
    except Exception as e:
        logger.error(f"Failed to expand instances for {master_event_id}: {e}")
        await db.rollback()
        return []
