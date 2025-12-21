"""
Tools Registry
Central registry for all LLM function calling tools
"""

from typing import List, Dict, Any, Callable, Optional
import json
from datetime import datetime, timedelta

from app.core.logging import logger
from app.schemas.tools import Tool, ToolFunction, ToolProperties, ToolParameter


class ToolsRegistry:
    """Registry for managing LLM tools"""
    
    def __init__(self):
        self.tools: Dict[str, Callable] = {}
        self.tool_schemas: List[Tool] = []
        self._register_all_tools()
    
    def _register_all_tools(self):
        """Register all available tools"""
        # Register calendar tools
        self.register_tool("get_upcoming_events", self._get_upcoming_events_schema())
        self.register_tool("create_calendar_event", self._create_event_schema())
        self.register_tool("update_calendar_event", self._update_event_schema())
        self.register_tool("delete_calendar_event", self._delete_event_schema())
        self.register_tool("search_calendar_events", self._search_events_schema())
        
        logger.info(f"📋 Registered {len(self.tools)} tools")
    
    def register_tool(self, name: str, schema: Tool):
        """
        Register a tool with its schema
        
        Args:
            name: Tool name
            schema: Tool schema definition
        """
        self.tools[name] = name  # Store reference
        self.tool_schemas.append(schema)
        logger.debug(f"Registered tool: {name}")
    
    def get_all_tools(self) -> List[Tool]:
        """Get all registered tool schemas"""
        return self.tool_schemas
    
    def get_tool_schema(self, name: str) -> Optional[Tool]:
        """
        Get schema for specific tool
        
        Args:
            name: Tool name
            
        Returns:
            Tool schema or None
        """
        for tool in self.tool_schemas:
            if tool.function.name == name:
                return tool
        return None
    
    # ==================== TOOL SCHEMAS ====================
    
    def _get_upcoming_events_schema(self) -> Tool:
        """Schema for getting upcoming events"""
        return Tool(
            type="function",
            function=ToolFunction(
                name="get_upcoming_events",
                description="Get upcoming calendar events. Use this when user asks about their schedule, meetings, or events.",
                parameters=ToolProperties(
                    type="object",
                    properties={
                        "time_range": ToolParameter(
                            type="string",
                            description="Time range for events. Options: 'today', 'tomorrow', 'this_week', 'next_week', 'this_month'",
                            enum=["today", "tomorrow", "this_week", "next_week", "this_month"]
                        ),
                        "start_date": ToolParameter(
                            type="string",
                            description="Custom start date in ISO format (YYYY-MM-DD). Use this for specific date queries."
                        ),
                        "end_date": ToolParameter(
                            type="string",
                            description="Custom end date in ISO format (YYYY-MM-DD). Use with start_date for date ranges."
                        ),
                        "max_results": ToolParameter(
                            type="integer",
                            description="Maximum number of events to return (default: 10)"
                        )
                    },
                    required=[]
                )
            )
        )
    
    def _create_event_schema(self) -> Tool:
        """Schema for creating events"""
        return Tool(
            type="function",
            function=ToolFunction(
                name="create_calendar_event",
                description="Create a new calendar event. Use when user wants to schedule, create, or add an event. For RECURRING events (daily, weekly, monthly reminders), include the recurrence parameter.",
                parameters=ToolProperties(
                    type="object",
                    properties={
                        "summary": ToolParameter(
                            type="string",
                            description="Event title or summary (required)"
                        ),
                        "start_time": ToolParameter(
                            type="string",
                            description="Event start time in ISO format (YYYY-MM-DDTHH:MM:SS) (required)"
                        ),
                        "end_time": ToolParameter(
                            type="string",
                            description="Event end time in ISO format (YYYY-MM-DDTHH:MM:SS) (required)"
                        ),
                        "description": ToolParameter(
                            type="string",
                            description="Event description or notes (optional)"
                        ),
                        "location": ToolParameter(
                            type="string",
                            description="Event location (optional)"
                        ),
                        "attendees": ToolParameter(
                            type="array",
                            description="List of attendee email addresses (optional)"
                        ),
                        "recurrence": ToolParameter(
                            type="object",
                            description="For recurring/repeating events. Set frequency to DAILY, WEEKLY, MONTHLY, or YEARLY. Example: {\"frequency\": \"DAILY\"} for daily reminders. Can also include: interval (repeat every N periods), count (number of occurrences), until (end date ISO format), by_day (for weekly: [\"MO\",\"TU\",\"WE\",\"TH\",\"FR\"])"
                        )
                    },
                    required=["summary", "start_time", "end_time"]
                )
            )
        )
    
    def _update_event_schema(self) -> Tool:
        """Schema for updating events"""
        return Tool(
            type="function",
            function=ToolFunction(
                name="update_calendar_event",
                description="Update an existing calendar event. Use when user wants to modify, change, or reschedule an event.",
                parameters=ToolProperties(
                    type="object",
                    properties={
                        "event_id": ToolParameter(
                            type="string",
                            description="Google Calendar event ID (required). Get this from search results or event list."
                        ),
                        "summary": ToolParameter(
                            type="string",
                            description="New event title (optional)"
                        ),
                        "start_time": ToolParameter(
                            type="string",
                            description="New start time in ISO format (optional)"
                        ),
                        "end_time": ToolParameter(
                            type="string",
                            description="New end time in ISO format (optional)"
                        ),
                        "description": ToolParameter(
                            type="string",
                            description="New description (optional)"
                        ),
                        "location": ToolParameter(
                            type="string",
                            description="New location (optional)"
                        ),
                        "status": ToolParameter(
                            type="string",
                            description="Event status: 'confirmed', 'tentative', or 'cancelled' (optional)",
                            enum=["confirmed", "tentative", "cancelled"]
                        )
                    },
                    required=["event_id"]
                )
            )
        )
    
    def _delete_event_schema(self) -> Tool:
        """Schema for deleting events"""
        return Tool(
            type="function",
            function=ToolFunction(
                name="delete_calendar_event",
                description="Delete a calendar event. Use when user wants to cancel or remove an event.",
                parameters=ToolProperties(
                    type="object",
                    properties={
                        "event_id": ToolParameter(
                            type="string",
                            description="Google Calendar event ID to delete (required)"
                        )
                    },
                    required=["event_id"]
                )
            )
        )
    
    def _search_events_schema(self) -> Tool:
        """Schema for searching events"""
        return Tool(
            type="function",
            function=ToolFunction(
                name="search_calendar_events",
                description="Search for calendar events by keyword. Use when user asks to find specific events.",
                parameters=ToolProperties(
                    type="object",
                    properties={
                        "query": ToolParameter(
                            type="string",
                            description="Search query text (required)"
                        ),
                        "max_results": ToolParameter(
                            type="integer",
                            description="Maximum number of results (default: 10)"
                        )
                    },
                    required=["query"]
                )
            )
        )
    
    # ==================== HELPER FUNCTIONS ====================
    
    @staticmethod
    def parse_time_range(time_range: str) -> tuple:
        """
        Parse time range string to start and end dates
        
        Args:
            time_range: Time range identifier
            
        Returns:
            Tuple of (start_date, end_date)
        """
        now = datetime.utcnow()
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        
        if time_range == "today":
            start = today_start
            end = today_start + timedelta(days=1)
        
        elif time_range == "tomorrow":
            start = today_start + timedelta(days=1)
            end = today_start + timedelta(days=2)
        
        elif time_range == "this_week":
            # Start of week (Monday)
            days_since_monday = now.weekday()
            start = today_start - timedelta(days=days_since_monday)
            end = start + timedelta(days=7)
        
        elif time_range == "next_week":
            days_since_monday = now.weekday()
            start = today_start - timedelta(days=days_since_monday) + timedelta(days=7)
            end = start + timedelta(days=7)
        
        elif time_range == "this_month":
            start = today_start.replace(day=1)
            # Next month
            if start.month == 12:
                end = start.replace(year=start.year + 1, month=1)
            else:
                end = start.replace(month=start.month + 1)
        
        else:
            # Default: next 7 days
            start = now
            end = now + timedelta(days=7)
        
        return start, end
    
    @staticmethod
    def format_event_for_display(event: Dict[str, Any]) -> str:
        """
        Format event for natural language display
        
        Args:
            event: Event dictionary
            
        Returns:
            Formatted event string
        """
        summary = event.get('summary', 'Untitled Event')
        
        # Parse start time
        start = event.get('start', {})
        if 'dateTime' in start:
            start_dt = datetime.fromisoformat(start['dateTime'].replace('Z', '+00:00'))
            time_str = start_dt.strftime('%I:%M %p on %B %d, %Y')
        elif 'date' in start:
            start_dt = datetime.fromisoformat(start['date'])
            time_str = start_dt.strftime('%B %d, %Y') + " (All day)"
        else:
            time_str = "Unknown time"
        
        location = event.get('location', '')
        description = event.get('description', '')
        
        result = f"📅 {summary}\n⏰ {time_str}"
        
        if location:
            result += f"\n📍 {location}"
        
        if description:
            # Truncate long descriptions
            desc_preview = description[:100] + "..." if len(description) > 100 else description
            result += f"\n📝 {desc_preview}"
        
        return result


# Global instance
tools_registry = ToolsRegistry()
