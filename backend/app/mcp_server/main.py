"""MCP server — exposes our calendar tools to Hermes Agent.

Hermes connects via Server-Sent Events (SSE) transport on port 9000.
Each tool here is a thin wrapper around our existing calendar_service.

New tools (Drive, Maps, etc.) are added here as we build Phase 4+.

Run: python -m app.mcp_server.main
"""
import json
import logging
from datetime import datetime, timedelta, timezone

from fastmcp import FastMCP  # high-level FastMCP library (not mcp.server.fastmcp)

from app.core.config import settings
from app.core.logging import get_logger, setup_logging
from app.db.database import AsyncSessionLocal
from app.models.models import User
from app.services.calendar_service import calendar_service
from app.services.oauth_service import load_user_credentials

setup_logging()
logger = get_logger("mcp_server")

mcp = FastMCP(
    name="whatsapp-agent-tools",
    instructions=(
        "Tools for managing the user's Google Calendar. "
        "Always use ISO 8601 datetimes. Timezone is " + settings.TIMEZONE + ". "
        "When creating events with video calls, set create_meet_link=true."
    ),
)


async def _get_owner() -> User | None:
    async with AsyncSessionLocal() as db:
        from sqlalchemy import select
        result = await db.execute(
            select(User).where(User.wa_phone == settings.OWNER_WA_PHONE.lstrip("+"))
        )
        return result.scalar_one_or_none()


@mcp.tool()
async def list_upcoming_events(days_ahead: int = 7, max_results: int = 10) -> str:
    """List the user's upcoming Google Calendar events.

    Args:
        days_ahead: How many days forward to look (default 7).
        max_results: Max events to return (default 10).
    """
    async with AsyncSessionLocal() as db:
        user = await _get_owner()
        if not user:
            return json.dumps({"error": "Owner user not found"})
        events = await calendar_service.list_upcoming_events(
            db, user, max_results=max_results, days_ahead=days_ahead
        )
        simplified = [
            {
                "id": e.get("id"),
                "summary": e.get("summary"),
                "start": (e.get("start") or {}).get("dateTime") or (e.get("start") or {}).get("date"),
                "end": (e.get("end") or {}).get("dateTime") or (e.get("end") or {}).get("date"),
                "location": e.get("location"),
                "meet_link": next(
                    (
                        ep.get("uri")
                        for ep in (e.get("conferenceData") or {}).get("entryPoints", [])
                        if ep.get("entryPointType") == "video"
                    ),
                    None,
                ),
            }
            for e in events
        ]
        return json.dumps({"events": simplified, "count": len(simplified)})


@mcp.tool()
async def create_event(
    summary: str,
    start_time: str,
    end_time: str,
    description: str = "",
    location: str = "",
    attendees: list[str] | None = None,
    create_meet_link: bool = False,
) -> str:
    """Create a new Google Calendar event.

    Args:
        summary: Title of the event.
        start_time: ISO 8601 start datetime (e.g. 2026-05-27T19:00:00+05:30).
        end_time: ISO 8601 end datetime.
        description: Optional description.
        location: Optional location string.
        attendees: Optional list of attendee email addresses.
        create_meet_link: Set true for video calls / virtual meetings.
    """
    from dateutil import parser as dp
    async with AsyncSessionLocal() as db:
        user = await _get_owner()
        if not user:
            return json.dumps({"error": "Owner user not found"})
        try:
            start = dp.parse(start_time)
            end = dp.parse(end_time)
        except Exception as exc:
            return json.dumps({"error": f"Bad datetime: {exc}"})

        result = await calendar_service.create_event(
            db, user,
            summary=summary,
            start_time=start,
            end_time=end,
            description=description,
            location=location,
            attendees=attendees or [],
            create_meet_link=create_meet_link,
            source_chat="hermes-mcp",
        )
        if not result:
            return json.dumps({"error": "Failed to create event — check Google OAuth tokens"})
        return json.dumps(result)


@mcp.tool()
async def delete_event(google_event_id: str) -> str:
    """Delete a Google Calendar event by its ID.

    Args:
        google_event_id: The event ID returned by list_upcoming_events or create_event.
    """
    async with AsyncSessionLocal() as db:
        user = await _get_owner()
        if not user:
            return json.dumps({"error": "Owner user not found"})
        ok = await calendar_service.delete_event(db, user, google_event_id)
        return json.dumps({"deleted": ok})


@mcp.tool()
async def check_conflicts(start_time: str, end_time: str) -> str:
    """Check if a proposed time slot conflicts with existing events.

    Args:
        start_time: ISO 8601 start datetime.
        end_time: ISO 8601 end datetime.
    """
    from dateutil import parser as dp
    async with AsyncSessionLocal() as db:
        user = await _get_owner()
        if not user:
            return json.dumps({"error": "Owner user not found"})
        try:
            start = dp.parse(start_time)
            end = dp.parse(end_time)
        except Exception as exc:
            return json.dumps({"error": f"Bad datetime: {exc}"})

        conflicts = await calendar_service.find_conflicts(db, user, start, end)
        return json.dumps({
            "has_conflicts": len(conflicts) > 0,
            "conflicts": [
                {"id": c.id, "summary": c.summary, "start": c.start_time.isoformat()}
                for c in conflicts
            ],
        })


@mcp.tool()
async def get_current_time() -> str:
    """Return the current date and time in the owner's timezone.
    Use this before scheduling anything to avoid timezone errors."""
    now = datetime.now(timezone.utc).astimezone()
    return json.dumps({
        "iso": now.isoformat(),
        "human": now.strftime("%A, %d %B %Y — %I:%M %p %Z"),
        "timezone": settings.TIMEZONE,
    })


if __name__ == "__main__":
    import uvicorn
    logger.info("MCP server starting on port 9000")
    # FastMCP 3.x: http_app returns a Starlette app for HTTP/SSE transport
    asgi_app = mcp.http_app(transport="sse")
    uvicorn.run(asgi_app, host="0.0.0.0", port=9000, log_level="info")
