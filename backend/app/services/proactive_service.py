"""Proactive messaging service.

Generates and schedules:
  - Per-event reminders (15min, 1hour, 1day before)
  - Morning briefing (configurable time)
  - Evening summary (configurable time)
  - Weekly insights (e.g. Monday 9am)
  - Conflict-detection nudges (when a new event overlaps with existing)
  - Free-form nudges (the agent proactively suggests things)

All proactive messages go to the OWNER's DM (settings.OWNER_WA_PHONE), except
event reminders for events whose source_chat is a group — in that case the
reminder optionally goes to the group too.
"""
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.db.redis_client import schedule_job
from app.models.models import EventCache, Reminder, User

logger = get_logger(__name__)


class ProactiveService:
    async def schedule_event_reminders(
        self, db: AsyncSession, user: User, event: EventCache
    ) -> int:
        """Create reminder rows + sorted-set jobs for a newly created event."""
        now = datetime.now(timezone.utc)
        scheduled = 0

        windows: list[tuple[str, timedelta, bool]] = [
            ("15min", timedelta(minutes=15), settings.REMINDER_15MIN_ENABLED),
            ("1hour", timedelta(hours=1), settings.REMINDER_1HOUR_ENABLED),
            ("1day", timedelta(days=1), settings.REMINDER_1DAY_ENABLED),
        ]

        for kind, delta, enabled in windows:
            if not enabled:
                continue
            run_at = event.start_time - delta
            if run_at <= now:
                continue

            reminder = Reminder(
                user_id=user.id,
                event_id=event.id,
                reminder_type=kind,
                scheduled_at=run_at,
                status="pending",
                payload={"event_id": event.id, "summary": event.summary},
            )
            db.add(reminder)
            await db.flush()

            await schedule_job(
                job_id=str(reminder.id),
                run_at_ts=run_at.timestamp(),
                payload={
                    "kind": "event_reminder",
                    "reminder_id": reminder.id,
                    "user_id": user.id,
                    "event_id": event.id,
                    "reminder_type": kind,
                },
            )
            scheduled += 1

        await db.commit()
        logger.info("Scheduled %d reminders for event %s", scheduled, event.id)
        return scheduled

    def format_event_reminder(self, kind: str, event: EventCache) -> str:
        when = event.start_time.strftime("%a %d %b, %I:%M %p")
        label = {"15min": "in 15 minutes", "1hour": "in 1 hour", "1day": "tomorrow"}.get(kind, "soon")
        lines = [f"[Reminder] {event.summary} is {label}.", f"When: {when}"]
        if event.location:
            lines.append(f"Where: {event.location}")
        if event.meet_link:
            lines.append(f"Meet: {event.meet_link}")
        return "\n".join(lines)

    def format_morning_briefing(self, events: list[EventCache]) -> str:
        if not events:
            return "Good morning. Nothing on your calendar today. Enjoy."
        lines = [f"Good morning. {len(events)} event(s) today:"]
        for e in events:
            t = e.start_time.strftime("%I:%M %p")
            lines.append(f"• {t} — {e.summary}")
        return "\n".join(lines)

    def format_evening_summary(self, tomorrow_events: list[EventCache]) -> str:
        if not tomorrow_events:
            return "Tomorrow is clear. Plan accordingly."
        lines = [f"Tomorrow you have {len(tomorrow_events)} event(s):"]
        for e in tomorrow_events:
            t = e.start_time.strftime("%I:%M %p")
            lines.append(f"• {t} — {e.summary}")
        return "\n".join(lines)

    def format_conflict_nudge(self, new_event: EventCache, conflicts: list[EventCache]) -> str:
        lines = [f"[Conflict] '{new_event.summary}' overlaps with:"]
        for c in conflicts:
            t = c.start_time.strftime("%a %I:%M %p")
            lines.append(f"• {t} — {c.summary}")
        lines.append("\nWant me to reschedule one of them?")
        return "\n".join(lines)

    async def upcoming_events(
        self, db: AsyncSession, user: User, window_start: datetime, window_end: datetime
    ) -> list[EventCache]:
        result = await db.execute(
            select(EventCache).where(
                and_(
                    EventCache.user_id == user.id,
                    EventCache.status == "confirmed",
                    EventCache.start_time >= window_start,
                    EventCache.start_time < window_end,
                )
            ).order_by(EventCache.start_time)
        )
        return list(result.scalars().all())

    async def schedule_daily_briefings(self, db: AsyncSession, user: User) -> None:
        """Schedule the next morning briefing and evening summary for a user."""
        from datetime import time
        now = datetime.now(timezone.utc)

        for time_str, kind in [
            (settings.MORNING_BRIEFING_TIME, "morning_briefing"),
            (settings.EVENING_SUMMARY_TIME, "evening_summary"),
        ]:
            try:
                hh, mm = map(int, time_str.split(":"))
            except Exception:
                continue
            target = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
            if target <= now:
                target = target + timedelta(days=1)

            await schedule_job(
                job_id=f"{kind}:{user.id}:{target.date().isoformat()}",
                run_at_ts=target.timestamp(),
                payload={"kind": kind, "user_id": user.id},
            )


proactive_service = ProactiveService()
