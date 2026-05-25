"""Scheduler worker — polls Redis Sorted Set for due jobs and dispatches them.

Run with:  python -m app.workers.scheduler_worker
"""
import asyncio
import signal
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.core.config import settings
from app.core.logging import get_logger, setup_logging
from app.db.database import AsyncSessionLocal
from app.db.redis_client import fetch_due_jobs, schedule_job
from app.models.models import EventCache, Reminder, User
from app.services.proactive_service import proactive_service
from app.services.whatsapp_service import whatsapp_service

logger = get_logger("scheduler_worker")
STOP = False
POLL_INTERVAL_SECONDS = 5


def _handle_stop(*_):
    global STOP
    STOP = True


async def _send_to_owner(message: str) -> None:
    await whatsapp_service.send_text(settings.OWNER_WA_PHONE, message)


async def _handle_event_reminder(payload: dict) -> None:
    reminder_id = payload.get("reminder_id")
    event_id = payload.get("event_id")
    kind = payload.get("reminder_type", "")

    async with AsyncSessionLocal() as db:
        rem_q = await db.execute(select(Reminder).where(Reminder.id == reminder_id))
        reminder = rem_q.scalar_one_or_none()
        if reminder is None or reminder.status != "pending":
            return

        ev_q = await db.execute(select(EventCache).where(EventCache.id == event_id))
        event = ev_q.scalar_one_or_none()
        if not event:
            reminder.status = "cancelled"
            await db.commit()
            return

        msg = proactive_service.format_event_reminder(kind, event)

        # Reminder always goes to the owner DM.
        # If the event came from a group AND it's the 15-min reminder, also nudge the group.
        await _send_to_owner(msg)
        if kind == "15min" and event.source_chat and "@g.us" in event.source_chat:
            await whatsapp_service.send_text(event.source_chat, msg)

        reminder.status = "sent"
        reminder.sent_at = datetime.now(timezone.utc)
        await db.commit()


async def _handle_morning_briefing(payload: dict) -> None:
    user_id = payload.get("user_id")
    async with AsyncSessionLocal() as db:
        user_q = await db.execute(select(User).where(User.id == user_id))
        user = user_q.scalar_one_or_none()
        if not user:
            return

        now = datetime.now(timezone.utc)
        end_of_day = now.replace(hour=23, minute=59, second=59)
        events = await proactive_service.upcoming_events(db, user, now, end_of_day)

        msg = proactive_service.format_morning_briefing(events)
        await _send_to_owner(msg)

    # Re-schedule for tomorrow
    async with AsyncSessionLocal() as db:
        user_q = await db.execute(select(User).where(User.id == user_id))
        user = user_q.scalar_one_or_none()
        if user:
            await proactive_service.schedule_daily_briefings(db, user)


async def _handle_evening_summary(payload: dict) -> None:
    user_id = payload.get("user_id")
    async with AsyncSessionLocal() as db:
        user_q = await db.execute(select(User).where(User.id == user_id))
        user = user_q.scalar_one_or_none()
        if not user:
            return

        now = datetime.now(timezone.utc)
        tomorrow_start = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        tomorrow_end = tomorrow_start + timedelta(days=1)
        events = await proactive_service.upcoming_events(db, user, tomorrow_start, tomorrow_end)

        msg = proactive_service.format_evening_summary(events)
        await _send_to_owner(msg)


async def _dispatch(job: dict) -> None:
    payload = job.get("payload") or {}
    kind = payload.get("kind")
    try:
        if kind == "event_reminder":
            await _handle_event_reminder(payload)
        elif kind == "morning_briefing":
            await _handle_morning_briefing(payload)
        elif kind == "evening_summary":
            await _handle_evening_summary(payload)
        else:
            logger.warning("Unknown job kind: %s", kind)
    except Exception as exc:
        logger.exception("Job dispatch failed (%s): %s", kind, exc)


async def _bootstrap_briefings() -> None:
    """On startup, ensure every existing user has a morning/evening briefing scheduled."""
    async with AsyncSessionLocal() as db:
        users_q = await db.execute(select(User))
        users = users_q.scalars().all()
        for u in users:
            try:
                await proactive_service.schedule_daily_briefings(db, u)
            except Exception as exc:
                logger.warning("Failed to schedule briefings for %s: %s", u.id, exc)


async def _main() -> None:
    setup_logging()
    signal.signal(signal.SIGTERM, _handle_stop)
    signal.signal(signal.SIGINT, _handle_stop)

    logger.info("Scheduler worker starting (poll interval %ds)", POLL_INTERVAL_SECONDS)
    await _bootstrap_briefings()

    while not STOP:
        now_ts = datetime.now(timezone.utc).timestamp()
        try:
            jobs = await fetch_due_jobs(now_ts, limit=50)
        except Exception as exc:
            logger.error("fetch_due_jobs failed: %s", exc)
            await asyncio.sleep(POLL_INTERVAL_SECONDS)
            continue

        for job in jobs:
            await _dispatch(job)

        await asyncio.sleep(POLL_INTERVAL_SECONDS)

    logger.info("Scheduler worker stopped")


if __name__ == "__main__":
    asyncio.run(_main())
