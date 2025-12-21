"""
Google Calendar Service
Handles all Google Calendar API operations with resilience patterns
"""

import asyncio
from functools import partial
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from google.oauth2.credentials import Credentials
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
import socket

from app.core.config import settings
from app.core.logging import logger
from app.core.circuit_breaker import CircuitBreaker, CircuitState
from app.core.retry import retry_with_backoff
from app.models.user import User, EventCache
from app.services.oauth_service import oauth_service
from app.schemas.calendar import CalendarEvent, CreateEventRequest, UpdateEventRequest


# Exceptions that are retryable (transient network issues)
RETRYABLE_EXCEPTIONS = (
    socket.timeout,
    ConnectionError,
    TimeoutError,
)


class CircuitOpenError(Exception):
    """Raised when circuit breaker is open"""
    pass


class CalendarService:
    """Service for Google Calendar API operations with circuit breaker and retry"""
    
    def __init__(self):
        self.api_name = "calendar"
        self.api_version = "v3"
        # Circuit breaker for Google Calendar API (using configurable settings)
        self._circuit_breaker = CircuitBreaker(
            name="google_calendar_api",
            failure_threshold=settings.CIRCUIT_FAILURE_THRESHOLD,
            success_threshold=settings.CIRCUIT_SUCCESS_THRESHOLD,
            timeout=settings.CIRCUIT_TIMEOUT_SECONDS
        )
    
    def _check_circuit(self) -> None:
        """Check if circuit breaker allows execution"""
        if not self._circuit_breaker.can_execute():
            logger.warning(f"Circuit breaker OPEN for Google Calendar API - request blocked")
            raise CircuitOpenError("Google Calendar API circuit breaker is open - service temporarily unavailable")
    
    def get_circuit_status(self) -> Dict[str, Any]:
        """Get circuit breaker status for monitoring"""
        return {
            "state": self._circuit_breaker.state.value,
            "failure_count": self._circuit_breaker.failure_count,
            "success_count": self._circuit_breaker.success_count,
            "last_failure_time": self._circuit_breaker.last_failure_time.isoformat() if self._circuit_breaker.last_failure_time else None
        }
    
    async def _run_sync(self, func, *args, **kwargs):
        """Run a synchronous function in a thread pool executor"""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, partial(func, *args, **kwargs))
    
    async def _run_with_resilience(self, sync_func, operation_name: str):
        """
        Run a synchronous Google API call with circuit breaker and retry
        
        Args:
            sync_func: Lambda or callable that performs the sync API call
            operation_name: Name of operation for logging
            
        Returns:
            Result from the API call
        """
        # Check circuit breaker first
        self._check_circuit()
        
        try:
            # Wrap sync call with retry
            async def execute_with_retry():
                return await self._run_sync(sync_func)
            
            # Use configurable retry settings
            result = await retry_with_backoff(
                execute_with_retry,
                max_retries=settings.RETRY_MAX_ATTEMPTS,
                base_delay=settings.RETRY_BASE_DELAY,
                max_delay=settings.RETRY_MAX_DELAY,
                retryable_exceptions=RETRYABLE_EXCEPTIONS
            )
            
            self._circuit_breaker.record_success()
            return result
            
        except HttpError as e:
            # 5xx errors are transient - record failure
            if e.resp.status >= 500:
                self._circuit_breaker.record_failure()
                logger.error(f"Google Calendar API server error: {e}")
            else:
                # 4xx errors are not transient - don't trigger circuit breaker
                logger.error(f"Google Calendar API client error: {e}")
            raise
            
        except RETRYABLE_EXCEPTIONS as e:
            self._circuit_breaker.record_failure()
            logger.error(f"Google Calendar API network error after retries: {e}")
            raise
            
        except Exception as e:
            self._circuit_breaker.record_failure()
            logger.error(f"Unexpected error in {operation_name}: {e}")
            raise
    
    async def _get_calendar_client(
        self,
        user: User,
        db: Session
    ):
        """
        Get authenticated Calendar API client
        
        Args:
            user: User object
            db: Database session
            
        Returns:
            Calendar API client
        """
        try:
            # Get valid credentials
            credentials = await oauth_service.get_valid_credentials(user, db)
            
            if not credentials:
                raise Exception("No valid credentials available")
            
            # Build Calendar API client
            service = build(
                self.api_name,
                self.api_version,
                credentials=credentials,
                cache_discovery=False
            )
            
            return service
            
        except Exception as e:
            logger.error(f"Failed to create Calendar client: {e}")
            raise
    
    async def list_events(
        self,
        user: User,
        db: Session,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        max_results: int = 10,
        query: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        List calendar events for user with resilience patterns
        
        Args:
            user: User object
            db: Database session
            start_date: Start of time range (default: now)
            end_date: End of time range (default: 7 days from now)
            max_results: Maximum number of events to return
            query: Optional search query
            
        Returns:
            List of event dictionaries
        """
        try:
            # Default time range: next 7 days
            if not start_date:
                start_date = datetime.utcnow()
            if not end_date:
                end_date = start_date + timedelta(days=7)
            
            logger.info(f"📅 Fetching events for {user.wa_phone}")
            logger.debug(f"Range: {start_date} to {end_date}, Max: {max_results}")
            
            # Get Calendar client
            service = await self._get_calendar_client(user, db)
            
            # Prepare parameters
            params = {
                'calendarId': 'primary',
                'timeMin': start_date.isoformat() + 'Z',
                'timeMax': end_date.isoformat() + 'Z',
                'maxResults': max_results,
                'singleEvents': True,
                'orderBy': 'startTime'
            }
            
            if query:
                params['q'] = query
            
            # Fetch events with resilience (circuit breaker + retry)
            events_result = await self._run_with_resilience(
                lambda: service.events().list(**params).execute(),
                "list_events"
            )
            events = events_result.get('items', [])
            
            logger.info(f"✅ Found {len(events)} events")
            
            # Cache events in database
            for event in events:
                await self._cache_event(user, event, db)
            
            await db.commit()
            
            return events
            
        except CircuitOpenError:
            logger.warning(f"Calendar API circuit open - returning empty events list")
            return []
        except HttpError as e:
            logger.error(f"Calendar API error: {e}")
            raise Exception(f"Failed to fetch events: {e}")
        except Exception as e:
            logger.error(f"Error listing events: {e}")
            raise
    
    async def create_event(
        self,
        user: User,
        db: Session,
        event_data: CreateEventRequest
    ) -> Dict[str, Any]:
        """
        Create a new calendar event with resilience patterns
        
        Args:
            user: User object
            db: Database session
            event_data: Event creation data
            
        Returns:
            Created event dictionary
        """
        try:
            logger.info(f"📅 Creating event for {user.wa_phone}: {event_data.summary}")
            
            # Get Calendar client
            service = await self._get_calendar_client(user, db)
            
            # Prepare event body
            event_body = {
                'summary': event_data.summary,
                'start': {
                    'dateTime': event_data.start_time.isoformat(),
                    'timeZone': 'UTC',
                },
                'end': {
                    'dateTime': event_data.end_time.isoformat(),
                    'timeZone': 'UTC',
                }
            }
            
            if event_data.description:
                event_body['description'] = event_data.description
            
            if event_data.location:
                event_body['location'] = event_data.location
            
            if event_data.attendees:
                event_body['attendees'] = [
                    {'email': email} for email in event_data.attendees
                ]
            
            # Create event with resilience (circuit breaker + retry)
            event = await self._run_with_resilience(
                lambda: service.events().insert(
                    calendarId='primary',
                    body=event_body
                ).execute(),
                "create_event"
            )
            
            logger.info(f"✅ Event created: {event.get('id')}")
            
            # Cache event
            await self._cache_event(user, event, db)
            await db.commit()
            
            return event
            
        except CircuitOpenError:
            raise Exception("Google Calendar API temporarily unavailable - please try again later")
        except HttpError as e:
            logger.error(f"Calendar API error: {e}")
            raise Exception(f"Failed to create event: {e}")
        except Exception as e:
            logger.error(f"Error creating event: {e}")
            raise
    
    async def update_event(
        self,
        user: User,
        db: Session,
        event_data: UpdateEventRequest
    ) -> Dict[str, Any]:
        """
        Update an existing calendar event with resilience patterns
        
        Args:
            user: User object
            db: Database session
            event_data: Event update data
            
        Returns:
            Updated event dictionary
        """
        try:
            logger.info(f"📅 Updating event {event_data.event_id} for {user.wa_phone}")
            
            # Get Calendar client
            service = await self._get_calendar_client(user, db)
            
            # Fetch existing event with resilience
            event = await self._run_with_resilience(
                lambda: service.events().get(
                    calendarId='primary',
                    eventId=event_data.event_id
                ).execute(),
                "get_event_for_update"
            )
            
            # Apply updates
            if event_data.summary:
                event['summary'] = event_data.summary
            
            if event_data.description is not None:
                event['description'] = event_data.description
            
            if event_data.location is not None:
                event['location'] = event_data.location
            
            if event_data.start_time:
                event['start'] = {
                    'dateTime': event_data.start_time.isoformat(),
                    'timeZone': 'UTC',
                }
            
            if event_data.end_time:
                event['end'] = {
                    'dateTime': event_data.end_time.isoformat(),
                    'timeZone': 'UTC',
                }
            
            if event_data.attendees is not None:
                event['attendees'] = [
                    {'email': email} for email in event_data.attendees
                ]
            
            if event_data.status:
                event['status'] = event_data.status
            
            # Update event with resilience
            updated_event = await self._run_with_resilience(
                lambda: service.events().update(
                    calendarId='primary',
                    eventId=event_data.event_id,
                    body=event
                ).execute(),
                "update_event"
            )
            
            logger.info(f"✅ Event updated: {updated_event.get('id')}")
            
            # Update cache
            await self._cache_event(user, updated_event, db)
            await db.commit()
            
            return updated_event
            
        except CircuitOpenError:
            raise Exception("Google Calendar API temporarily unavailable - please try again later")
        except HttpError as e:
            logger.error(f"Calendar API error: {e}")
            raise Exception(f"Failed to update event: {e}")
        except Exception as e:
            logger.error(f"Error updating event: {e}")
            raise
    
    async def delete_event(
        self,
        user: User,
        db: Session,
        event_id: str
    ) -> bool:
        """
        Delete a calendar event with resilience patterns
        
        Args:
            user: User object
            db: Database session
            event_id: Google Calendar event ID
            
        Returns:
            True if successful
        """
        try:
            logger.info(f"📅 Deleting event {event_id} for {user.wa_phone}")
            
            # Get Calendar client
            service = await self._get_calendar_client(user, db)
            
            # Delete event with resilience
            await self._run_with_resilience(
                lambda: service.events().delete(
                    calendarId='primary',
                    eventId=event_id
                ).execute(),
                "delete_event"
            )
            
            logger.info(f"✅ Event deleted: {event_id}")
            
            # Remove from cache
            from sqlalchemy import select
            query = select(EventCache).where(
                EventCache.user_id == user.id,
                EventCache.google_event_id == event_id
            )
            result = await db.execute(query)
            cached_event = result.scalar_one_or_none()
            
            if cached_event:
                await db.delete(cached_event)
                await db.commit()
            
            return True
            
        except CircuitOpenError:
            raise Exception("Google Calendar API temporarily unavailable - please try again later")
        except HttpError as e:
            logger.error(f"Calendar API error: {e}")
            raise Exception(f"Failed to delete event: {e}")
        except Exception as e:
            logger.error(f"Error deleting event: {e}")
            raise
    
    async def search_events(
        self,
        user: User,
        db: Session,
        query: str,
        max_results: int = 10
    ) -> List[Dict[str, Any]]:
        """
        Search calendar events
        
        Args:
            user: User object
            db: Database session
            query: Search query
            max_results: Maximum results
            
        Returns:
            List of matching events
        """
        try:
            logger.info(f"🔍 Searching events for {user.wa_phone}: '{query}'")
            
            # Use list_events with query parameter
            events = await self.list_events(
                user=user,
                db=db,
                max_results=max_results,
                query=query
            )
            
            logger.info(f"✅ Found {len(events)} matching events")
            
            return events
            
        except Exception as e:
            logger.error(f"Error searching events: {e}")
            raise
    
    async def _cache_event(
        self,
        user: User,
        event: Dict[str, Any],
        db
    ) -> None:
        """
        Cache event in database
        
        Args:
            user: User object
            event: Event dictionary from Google Calendar
            db: Database session
        """
        try:
            google_event_id = event.get('id')
            
            if not google_event_id:
                return
            
            # Parse dates
            start = event.get('start', {})
            end = event.get('end', {})
            
            start_time = None
            end_time = None
            
            if 'dateTime' in start:
                start_time = datetime.fromisoformat(start['dateTime'].replace('Z', '+00:00'))
                # Convert to naive datetime (remove timezone info for DB storage)
                if start_time.tzinfo is not None:
                    start_time = start_time.replace(tzinfo=None)
            elif 'date' in start:
                start_time = datetime.fromisoformat(start['date'])
            
            if 'dateTime' in end:
                end_time = datetime.fromisoformat(end['dateTime'].replace('Z', '+00:00'))
                # Convert to naive datetime (remove timezone info for DB storage)
                if end_time.tzinfo is not None:
                    end_time = end_time.replace(tzinfo=None)
            elif 'date' in end:
                end_time = datetime.fromisoformat(end['date'])
            
            if not start_time or not end_time:
                return
            
            # Get or create cache entry
            from sqlalchemy import select
            query = select(EventCache).where(
                EventCache.user_id == user.id,
                EventCache.google_event_id == google_event_id
            )
            result = await db.execute(query)
            cached_event = result.scalar_one_or_none()
            
            if not cached_event:
                cached_event = EventCache(
                    user_id=user.id,
                    google_event_id=google_event_id
                )
                db.add(cached_event)
            
            # Update fields
            cached_event.summary = event.get('summary')
            cached_event.description = event.get('description')
            cached_event.location = event.get('location')
            cached_event.start_time = start_time
            cached_event.end_time = end_time
            cached_event.status = event.get('status', 'confirmed')
            
            # Store attendees
            attendees = event.get('attendees', [])
            cached_event.attendees = [a.get('email') for a in attendees]
            
            logger.debug(f"Cached event: {google_event_id}")
            
        except Exception as e:
            logger.error(f"Error caching event: {e}")


# Global instance
calendar_service = CalendarService()
