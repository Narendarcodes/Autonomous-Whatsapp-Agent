"""Google Calendar service — CRUD + caching + conflict detection."""
import asyncio
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.models import EventCache, User
from app.services.oauth_service import load_user_credentials

logger = get_logger(__name__)


def _build_service(creds):
    return build("calendar", "v3", credentials=creds, cache_discovery=False)


async def _run_blocking(fn, *args, **kwargs):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, lambda: fn(*args, **kwargs))


class CalendarService:
    async def list_upcoming_events(
        self,
        db: AsyncSession,
        user: User,
        max_results: int = 10,
        days_ahead: int = 7,
    ) -> list[dict]:
        creds = await load_user_credentials(user, db)
        if not creds:
            return []

        now = datetime.now(timezone.utc)
        time_max = now + timedelta(days=days_ahead)

        service = _build_service(creds)
        try:
            events_result = await _run_blocking(
                lambda: service.events()
                .list(
                    calendarId="primary",
                    timeMin=now.isoformat(),
                    timeMax=time_max.isoformat(),
                    maxResults=max_results,
                    singleEvents=True,
                    orderBy="startTime",
                )
                .execute()
            )
            return events_result.get("items", [])
        except HttpError as exc:
            logger.error("list_upcoming_events failed: %s", exc)
            return []

    async def create_event(
        self,
        db: AsyncSession,
        user: User,
        summary: str,
        start_time: datetime,
        end_time: datetime,
        description: str = "",
        location: str = "",
        attendees: list[str] | None = None,
        create_meet_link: bool = False,
        source_chat: str | None = None,
    ) -> dict | None:
        creds = await load_user_credentials(user, db)
        if not creds:
            logger.warning("No Google credentials for user %s", user.id)
            return None

        body: dict = {
            "summary": summary,
            "description": description,
            "location": location,
            "start": {"dateTime": start_time.isoformat(), "timeZone": user.timezone or "UTC"},
            "end": {"dateTime": end_time.isoformat(), "timeZone": user.timezone or "UTC"},
        }
        if attendees:
            body["attendees"] = [{"email": e} for e in attendees]
        if create_meet_link:
            body["conferenceData"] = {
                "createRequest": {
                    "requestId": str(uuid4()),
                    "conferenceSolutionKey": {"type": "hangoutsMeet"},
                }
            }

        kwargs = {"conferenceDataVersion": 1} if create_meet_link else {}

        service = _build_service(creds)
        try:
            result = await _run_blocking(
                lambda: service.events()
                .insert(calendarId="primary", body=body, **kwargs)
                .execute()
            )
        except HttpError as exc:
            logger.error("create_event failed: %s", exc)
            return None

        meet_link = None
        for ep in (result.get("conferenceData") or {}).get("entryPoints", []):
            if ep.get("entryPointType") == "video":
                meet_link = ep.get("uri")
                break

        cached = EventCache(
            user_id=user.id,
            google_event_id=result["id"],
            summary=summary,
            description=description,
            location=location,
            start_time=start_time,
            end_time=end_time,
            attendees=attendees or [],
            meet_link=meet_link,
            source_chat=source_chat,
            status="confirmed",
        )
        db.add(cached)
        await db.commit()
        await db.refresh(cached)

        return {
            "id": cached.id,
            "google_event_id": result["id"],
            "summary": summary,
            "start": start_time.isoformat(),
            "end": end_time.isoformat(),
            "meet_link": meet_link,
            "html_link": result.get("htmlLink"),
        }

    async def delete_event(self, db: AsyncSession, user: User, google_event_id: str) -> bool:
        creds = await load_user_credentials(user, db)
        if not creds:
            return False

        service = _build_service(creds)
        try:
            await _run_blocking(
                lambda: service.events()
                .delete(calendarId="primary", eventId=google_event_id)
                .execute()
            )
        except HttpError as exc:
            logger.error("delete_event failed: %s", exc)
            return False

        result = await db.execute(
            select(EventCache).where(
                and_(EventCache.user_id == user.id, EventCache.google_event_id == google_event_id)
            )
        )
        cached = result.scalar_one_or_none()
        if cached:
            await db.delete(cached)
            await db.commit()
        return True

    async def find_conflicts(
        self, db: AsyncSession, user: User, start: datetime, end: datetime
    ) -> list[EventCache]:
        result = await db.execute(
            select(EventCache).where(
                and_(
                    EventCache.user_id == user.id,
                    EventCache.status == "confirmed",
                    EventCache.start_time < end,
                    EventCache.end_time > start,
                )
            )
        )
        return list(result.scalars().all())


calendar_service = CalendarService()
