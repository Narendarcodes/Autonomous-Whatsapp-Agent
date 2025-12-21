"""
Pydantic Schemas for Google Calendar
Event validation and serialization
"""

from pydantic import BaseModel, Field, validator
from typing import Optional, List
from datetime import datetime


class EventDateTime(BaseModel):
    """Calendar event date/time"""
    dateTime: Optional[str] = None  # RFC3339 format
    date: Optional[str] = None  # For all-day events (YYYY-MM-DD)
    timeZone: Optional[str] = "UTC"
    
    @validator('dateTime', 'date')
    def at_least_one_required(cls, v, values):
        """Ensure either dateTime or date is provided"""
        if not v and 'dateTime' not in values and 'date' not in values:
            raise ValueError('Either dateTime or date must be provided')
        return v


class EventAttendee(BaseModel):
    """Calendar event attendee"""
    email: str
    displayName: Optional[str] = None
    responseStatus: Optional[str] = "needsAction"  # needsAction, declined, tentative, accepted
    optional: Optional[bool] = False


class CalendarEvent(BaseModel):
    """Google Calendar event schema"""
    id: Optional[str] = None  # Google Calendar event ID
    summary: str = Field(..., description="Event title")
    description: Optional[str] = None
    location: Optional[str] = None
    start: EventDateTime
    end: EventDateTime
    attendees: Optional[List[EventAttendee]] = None
    status: Optional[str] = "confirmed"  # confirmed, tentative, cancelled
    created: Optional[datetime] = None
    updated: Optional[datetime] = None
    
    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }


class CreateEventRequest(BaseModel):
    """Request to create a calendar event"""
    summary: str = Field(..., min_length=1, max_length=500, description="Event title")
    description: Optional[str] = Field(None, max_length=2000)
    location: Optional[str] = Field(None, max_length=500)
    start_time: datetime = Field(..., description="Event start time (ISO format)")
    end_time: datetime = Field(..., description="Event end time (ISO format)")
    attendees: Optional[List[str]] = Field(None, description="List of attendee emails")
    
    @validator('end_time')
    def end_after_start(cls, v, values):
        """Ensure end time is after start time"""
        if 'start_time' in values and v <= values['start_time']:
            raise ValueError('End time must be after start time')
        return v
    
    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }


class UpdateEventRequest(BaseModel):
    """Request to update a calendar event"""
    event_id: str = Field(..., description="Google Calendar event ID")
    summary: Optional[str] = Field(None, min_length=1, max_length=500)
    description: Optional[str] = Field(None, max_length=2000)
    location: Optional[str] = Field(None, max_length=500)
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    attendees: Optional[List[str]] = None
    status: Optional[str] = Field(None, pattern="^(confirmed|tentative|cancelled)$")


class GetEventsRequest(BaseModel):
    """Request to get calendar events"""
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    max_results: Optional[int] = Field(10, ge=1, le=100)
    query: Optional[str] = None  # Search query
    
    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }


class EventsResponse(BaseModel):
    """Response with list of events"""
    events: List[CalendarEvent]
    total_count: int
    has_more: bool = False


class EventOperationResponse(BaseModel):
    """Response for event operations (create/update/delete)"""
    success: bool
    message: str
    event: Optional[CalendarEvent] = None
    error: Optional[str] = None
