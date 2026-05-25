"""
Pydantic Schemas for LLM Tools
Function calling definitions
"""

from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any, Literal
from datetime import datetime


# ==================== TOOL DEFINITIONS ====================

class ToolParameter(BaseModel):
    """Tool parameter definition"""
    type: str
    description: str
    enum: Optional[List[str]] = None


class ToolProperties(BaseModel):
    """Tool properties schema"""
    properties: Dict[str, ToolParameter]
    required: List[str]
    type: Literal["object"] = "object"


class ToolFunction(BaseModel):
    """Tool function definition"""
    name: str
    description: str
    parameters: ToolProperties


class Tool(BaseModel):
    """Complete tool definition"""
    type: Literal["function"] = "function"
    function: ToolFunction


# ==================== TOOL CALL SCHEMAS ====================

class ToolCall(BaseModel):
    """LLM tool call"""
    id: str
    type: Literal["function"] = "function"
    function: Dict[str, Any]  # {"name": "...", "arguments": "..."}


class ToolCallArguments(BaseModel):
    """Base for tool arguments"""
    pass


# ==================== GET EVENTS TOOL ====================

class GetEventsArguments(ToolCallArguments):
    """Arguments for get_events tool"""
    start_date: Optional[str] = Field(None, description="Start date in ISO format")
    end_date: Optional[str] = Field(None, description="End date in ISO format")
    time_range: Optional[str] = Field(
        None,
        description="Time range: today, tomorrow, this_week, next_week, this_month",
        pattern="^(today|tomorrow|this_week|next_week|this_month)$"
    )
    max_results: Optional[int] = Field(10, ge=1, le=50)


# ==================== CREATE EVENT TOOL ====================

class RecurrenceArgs(BaseModel):
    """Recurrence arguments for recurring events"""
    frequency: str = Field(..., description="DAILY, WEEKLY, MONTHLY, or YEARLY")
    interval: Optional[int] = Field(1, ge=1, le=365)
    count: Optional[int] = Field(None, ge=1, le=365)
    until: Optional[str] = Field(None, description="End date in ISO format")
    by_day: Optional[List[str]] = Field(None, description="Days of week: MO, TU, WE, TH, FR, SA, SU")
    by_month_day: Optional[List[int]] = Field(None, description="Days of month: 1-31")


class CreateEventArguments(ToolCallArguments):
    """Arguments for create_event tool"""
    summary: str = Field(..., description="Event title/summary", min_length=1, max_length=500)
    start_time: str = Field(..., description="Event start time (ISO format)")
    end_time: str = Field(..., description="Event end time (ISO format)")
    description: Optional[str] = Field(None, max_length=5000)
    location: Optional[str] = Field(None, max_length=500)
    attendees: Optional[List[str]] = Field(None, description="List of attendee emails", max_length=50)
    recurrence: Optional[RecurrenceArgs] = Field(None, description="Recurrence rules for repeating events")


# ==================== UPDATE EVENT TOOL ====================

class UpdateEventArguments(ToolCallArguments):
    """Arguments for update_event tool"""
    event_id: str = Field(..., description="Google Calendar event ID", min_length=1, max_length=200)
    summary: Optional[str] = Field(None, max_length=500)
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    description: Optional[str] = Field(None, max_length=5000)
    location: Optional[str] = Field(None, max_length=500)
    attendees: Optional[List[str]] = Field(None, max_length=50)
    status: Optional[str] = Field(None, description="confirmed, tentative, or cancelled", pattern="^(confirmed|tentative|cancelled)$")


# ==================== DELETE EVENT TOOL ====================

class DeleteEventArguments(ToolCallArguments):
    """Arguments for delete_event tool"""
    event_id: str = Field(..., description="Google Calendar event ID", min_length=1, max_length=200)


# ==================== SEARCH EVENTS TOOL ====================

class SearchEventsArguments(ToolCallArguments):
    """Arguments for search_events tool"""
    query: str = Field(..., description="Search query", min_length=1, max_length=500)
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    max_results: Optional[int] = Field(10, ge=1, le=50)


# ==================== TOOL EXECUTION RESULT ====================

class ToolExecutionResult(BaseModel):
    """Result of tool execution"""
    tool_call_id: str
    tool_name: str
    success: bool
    result: Any  # The actual result data
    error: Optional[str] = None
    execution_time: Optional[float] = None  # Time in seconds


# ==================== AGENT REQUEST/RESPONSE ====================

class AgentRequest(BaseModel):
    """Request to the agent"""
    user_id: str
    message: str
    conversation_history: Optional[List[Dict[str, str]]] = None


class AgentResponse(BaseModel):
    """Response from the agent"""
    response_text: str
    tool_calls: Optional[List[ToolCall]] = None
    tool_results: Optional[List[ToolExecutionResult]] = None
    needs_oauth: bool = False
    oauth_url: Optional[str] = None
