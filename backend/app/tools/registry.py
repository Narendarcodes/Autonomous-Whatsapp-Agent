"""Tool definitions exposed to the LLM via function calling."""
from datetime import datetime

from dateutil import parser as date_parser
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.models import User
from app.services.calendar_service import calendar_service

logger = get_logger(__name__)

# Tool schemas in OpenAI function-calling format (works for GitHub Models too).
TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "list_upcoming_events",
            "description": "List the user's upcoming Google Calendar events.",
            "parameters": {
                "type": "object",
                "properties": {
                    "days_ahead": {"type": "integer", "description": "Look this many days ahead. Default 7.", "default": 7},
                    "max_results": {"type": "integer", "description": "Maximum number of events to return.", "default": 10},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_event",
            "description": (
                "Create a new Google Calendar event. Use create_meet_link=true when the user "
                "mentions a video call, video meeting, virtual meeting, or Google Meet."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": {"type": "string", "description": "Title of the event."},
                    "start_time": {"type": "string", "description": "ISO 8601 start time (e.g. 2026-05-27T19:00:00+05:30)."},
                    "end_time": {"type": "string", "description": "ISO 8601 end time."},
                    "description": {"type": "string", "default": ""},
                    "location": {"type": "string", "default": ""},
                    "attendees": {"type": "array", "items": {"type": "string"}, "default": []},
                    "create_meet_link": {"type": "boolean", "default": False},
                },
                "required": ["summary", "start_time", "end_time"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_event",
            "description": "Delete a Google Calendar event by its google_event_id.",
            "parameters": {
                "type": "object",
                "properties": {
                    "google_event_id": {"type": "string"},
                },
                "required": ["google_event_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_conflicts",
            "description": "Check if a proposed time range conflicts with existing events.",
            "parameters": {
                "type": "object",
                "properties": {
                    "start_time": {"type": "string"},
                    "end_time": {"type": "string"},
                },
                "required": ["start_time", "end_time"],
            },
        },
    },
]


def _parse_dt(value: str) -> datetime:
    return date_parser.parse(value)


async def execute_tool(
    name: str,
    arguments: dict,
    db: AsyncSession,
    user: User,
    source_chat: str | None = None,
) -> dict:
    try:
        if name == "list_upcoming_events":
            events = await calendar_service.list_upcoming_events(
                db,
                user,
                max_results=int(arguments.get("max_results", 10)),
                days_ahead=int(arguments.get("days_ahead", 7)),
            )
            simplified = [
                {
                    "id": e.get("id"),
                    "summary": e.get("summary"),
                    "start": (e.get("start") or {}).get("dateTime") or (e.get("start") or {}).get("date"),
                    "end": (e.get("end") or {}).get("dateTime") or (e.get("end") or {}).get("date"),
                    "location": e.get("location"),
                    "meet_link": (e.get("conferenceData") or {}).get("entryPoints", [{}])[0].get("uri")
                    if e.get("conferenceData") else None,
                }
                for e in events
            ]
            return {"events": simplified, "count": len(simplified)}

        if name == "create_event":
            start = _parse_dt(arguments["start_time"])
            end = _parse_dt(arguments["end_time"])
            result = await calendar_service.create_event(
                db,
                user,
                summary=arguments["summary"],
                start_time=start,
                end_time=end,
                description=arguments.get("description", ""),
                location=arguments.get("location", ""),
                attendees=arguments.get("attendees") or [],
                create_meet_link=bool(arguments.get("create_meet_link", False)),
                source_chat=source_chat,
            )
            return result or {"error": "Failed to create event"}

        if name == "delete_event":
            ok = await calendar_service.delete_event(db, user, arguments["google_event_id"])
            return {"deleted": ok}

        if name == "check_conflicts":
            start = _parse_dt(arguments["start_time"])
            end = _parse_dt(arguments["end_time"])
            conflicts = await calendar_service.find_conflicts(db, user, start, end)
            return {
                "has_conflicts": len(conflicts) > 0,
                "conflicts": [
                    {"id": c.id, "summary": c.summary, "start": c.start_time.isoformat()}
                    for c in conflicts
                ],
            }

        return {"error": f"Unknown tool: {name}"}
    except Exception as exc:
        logger.exception("Tool %s failed: %s", name, exc)
        return {"error": str(exc)}
