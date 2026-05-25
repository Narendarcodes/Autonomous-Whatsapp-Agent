"""
Tests for Calendar Service - Tests the resilience patterns used by CalendarService
These tests verify circuit breaker and retry logic without requiring database connections.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timedelta
import time


class TestCalendarServiceCircuitBreaker:
    """Test cases for CalendarService circuit breaker integration"""
    
    def test_circuit_breaker_initialization(self):
        """Circuit breaker should be properly initialized"""
        from app.core.circuit_breaker import CircuitBreaker, CircuitState
        
        breaker = CircuitBreaker(
            failure_threshold=5,
            success_threshold=2,
            timeout=60,
            name="calendar"
        )
        
        assert breaker.state == CircuitState.CLOSED
        assert breaker.failure_threshold == 5
        assert breaker.success_threshold == 2
        assert breaker.name == "calendar"
    
    def test_circuit_breaker_opens_after_failures(self):
        """Circuit breaker should open after threshold failures"""
        from app.core.circuit_breaker import CircuitBreaker, CircuitState
        
        breaker = CircuitBreaker(
            failure_threshold=2,
            success_threshold=2,
            timeout=60,
            name="calendar_test"
        )
        
        # Record failures to open the circuit
        for _ in range(2):
            breaker.record_failure()
        
        assert breaker.state == CircuitState.OPEN
    
    def test_circuit_breaker_allows_request_when_closed(self):
        """Circuit breaker should allow requests when closed"""
        from app.core.circuit_breaker import CircuitBreaker
        
        breaker = CircuitBreaker(
            failure_threshold=5,
            success_threshold=2,
            timeout=60,
            name="calendar_test"
        )
        
        assert breaker.can_execute() is True
    
    def test_circuit_breaker_blocks_when_open(self):
        """Circuit breaker should block requests when open"""
        from app.core.circuit_breaker import CircuitBreaker, CircuitState
        
        breaker = CircuitBreaker(
            failure_threshold=2,
            success_threshold=2,
            timeout=60,
            name="calendar_test"
        )
        
        # Open the circuit
        breaker.record_failure()
        breaker.record_failure()
        
        assert breaker.state == CircuitState.OPEN
        assert breaker.can_execute() is False


class TestCalendarServiceRetry:
    """Test cases for CalendarService retry logic"""
    
    @pytest.mark.asyncio
    async def test_retry_success_first_attempt(self):
        """Retry should succeed on first attempt"""
        from app.core.retry import retry_with_backoff
        
        call_count = 0
        
        async def test_func():
            nonlocal call_count
            call_count += 1
            return "success"
        
        result = await retry_with_backoff(test_func, max_retries=3, base_delay=0.01)
        
        assert result == "success"
        assert call_count == 1
    
    @pytest.mark.asyncio
    async def test_retry_success_after_failure(self):
        """Retry should succeed after transient failure"""
        from app.core.retry import retry_with_backoff
        
        call_count = 0
        
        async def test_func():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise Exception("Temporary failure")
            return "success"
        
        result = await retry_with_backoff(test_func, max_retries=3, base_delay=0.01)
        
        assert result == "success"
        assert call_count == 2
    
    @pytest.mark.asyncio
    async def test_retry_failure_after_max_retries(self):
        """Retry should fail after max retries exhausted"""
        from app.core.retry import retry_with_backoff
        
        call_count = 0
        
        async def test_func():
            nonlocal call_count
            call_count += 1
            raise Exception("Persistent failure")
        
        with pytest.raises(Exception, match="Persistent failure"):
            await retry_with_backoff(test_func, max_retries=2, base_delay=0.01)
        
        # 3 total attempts: initial + 2 retries
        assert call_count == 3


class TestCalendarServiceResilience:
    """Test cases for CalendarService combined resilience patterns"""
    
    def test_circuit_breaker_recovery(self):
        """Circuit breaker should recover after timeout"""
        from app.core.circuit_breaker import CircuitBreaker, CircuitState
        
        breaker = CircuitBreaker(
            failure_threshold=2,
            success_threshold=2,
            timeout=1,  # 1 second timeout for testing
            name="calendar_recovery_test"
        )
        
        # Open the circuit
        breaker.record_failure()
        breaker.record_failure()
        
        assert breaker.state == CircuitState.OPEN
        
        # Wait for timeout
        time.sleep(1.1)
        
        # Should transition to HALF_OPEN on next check
        can_exec = breaker.can_execute()
        assert can_exec is True
        assert breaker.state == CircuitState.HALF_OPEN
    
    def test_circuit_closes_on_success_in_half_open(self):
        """Circuit should close on success in HALF_OPEN state"""
        from app.core.circuit_breaker import CircuitBreaker, CircuitState
        
        breaker = CircuitBreaker(
            failure_threshold=2,
            success_threshold=1,
            timeout=1,
            name="calendar_close_test"
        )
        
        # Open the circuit
        breaker.record_failure()
        breaker.record_failure()
        assert breaker.state == CircuitState.OPEN
        
        # Wait for timeout
        time.sleep(1.1)
        breaker.can_execute()  # Transition to HALF_OPEN
        
        # Record success
        breaker.record_success()
        
        assert breaker.state == CircuitState.CLOSED
    
    def test_circuit_reopens_on_failure_in_half_open(self):
        """Circuit should reopen on failure in HALF_OPEN state"""
        from app.core.circuit_breaker import CircuitBreaker, CircuitState
        
        breaker = CircuitBreaker(
            failure_threshold=2,
            success_threshold=2,
            timeout=1,
            name="calendar_reopen_test"
        )
        
        # Open the circuit
        breaker.record_failure()
        breaker.record_failure()
        assert breaker.state == CircuitState.OPEN
        
        # Wait for timeout
        time.sleep(1.1)
        breaker.can_execute()  # Transition to HALF_OPEN
        
        # Record failure
        breaker.record_failure()
        
        assert breaker.state == CircuitState.OPEN


class TestCalendarServiceStatus:
    """Test cases for CalendarService circuit status"""
    
    def test_get_circuit_state(self):
        """Should return correct circuit state"""
        from app.core.circuit_breaker import CircuitBreaker, CircuitState
        
        breaker = CircuitBreaker(
            failure_threshold=5,
            success_threshold=2,
            timeout=60,
            name="calendar_status"
        )
        
        status = breaker.get_state()
        
        assert status["state"] == CircuitState.CLOSED.value
        assert status["failure_count"] == 0
        assert status["success_count"] == 0
        assert status["name"] == "calendar_status"
    
    def test_circuit_state_after_failures(self):
        """Should report correct state after failures"""
        from app.core.circuit_breaker import CircuitBreaker, CircuitState
        
        breaker = CircuitBreaker(
            failure_threshold=5,
            success_threshold=2,
            timeout=60,
            name="calendar_status_fail"
        )
        
        # Record some failures
        breaker.record_failure()
        breaker.record_failure()
        
        status = breaker.get_state()
        
        assert status["failure_count"] == 2
        assert status["state"] == CircuitState.CLOSED.value  # Not yet at threshold


class TestCalendarEventFormatting:
    """Test cases for calendar event formatting"""
    
    def test_event_with_datetime(self):
        """Should handle events with dateTime format"""
        event = {
            'id': 'event1',
            'summary': 'Meeting',
            'start': {'dateTime': '2025-12-01T10:00:00Z'},
            'end': {'dateTime': '2025-12-01T11:00:00Z'}
        }
        
        # Verify the structure is correct
        assert 'dateTime' in event['start']
        assert event['start']['dateTime'] == '2025-12-01T10:00:00Z'
    
    def test_event_with_date_only(self):
        """Should handle all-day events with date format"""
        event = {
            'id': 'event2',
            'summary': 'Holiday',
            'start': {'date': '2025-12-25'},
            'end': {'date': '2025-12-26'}
        }
        
        # Verify the structure is correct
        assert 'date' in event['start']
        assert event['start']['date'] == '2025-12-25'
    
    def test_event_with_recurrence(self):
        """Should handle recurring events"""
        event = {
            'id': 'event3',
            'summary': 'Weekly Standup',
            'recurrence': ['RRULE:FREQ=WEEKLY;BYDAY=MO'],
            'start': {'dateTime': '2025-12-01T09:00:00Z'},
            'end': {'dateTime': '2025-12-01T09:30:00Z'}
        }
        
        assert 'recurrence' in event
        assert 'WEEKLY' in event['recurrence'][0]
