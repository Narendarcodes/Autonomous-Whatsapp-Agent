"""
Tests for Circuit Breaker Pattern
"""

import pytest
from datetime import datetime, timedelta
from app.core.circuit_breaker import CircuitBreaker, CircuitState


class TestCircuitBreaker:
    """Test cases for CircuitBreaker class"""
    
    def test_initial_state_is_closed(self):
        """Circuit breaker should start in CLOSED state"""
        breaker = CircuitBreaker(name="test", failure_threshold=3)
        assert breaker.state == CircuitState.CLOSED
        assert breaker.failure_count == 0
        assert breaker.success_count == 0
    
    def test_can_execute_when_closed(self):
        """Should allow execution when circuit is CLOSED"""
        breaker = CircuitBreaker(name="test", failure_threshold=3)
        assert breaker.can_execute() is True
    
    def test_records_success(self):
        """Should reset failure count on success"""
        breaker = CircuitBreaker(name="test", failure_threshold=3)
        breaker.failure_count = 2
        breaker.record_success()
        assert breaker.failure_count == 0
    
    def test_records_failure(self):
        """Should increment failure count"""
        breaker = CircuitBreaker(name="test", failure_threshold=3)
        breaker.record_failure()
        assert breaker.failure_count == 1
        assert breaker.last_failure_time is not None
    
    def test_opens_after_threshold(self):
        """Circuit should OPEN after reaching failure threshold"""
        breaker = CircuitBreaker(name="test", failure_threshold=3)
        
        # Record failures up to threshold
        for _ in range(3):
            breaker.record_failure()
        
        assert breaker.state == CircuitState.OPEN
        assert breaker.can_execute() is False
    
    def test_stays_closed_below_threshold(self):
        """Circuit should stay CLOSED below failure threshold"""
        breaker = CircuitBreaker(name="test", failure_threshold=3)
        
        breaker.record_failure()
        breaker.record_failure()
        
        assert breaker.state == CircuitState.CLOSED
        assert breaker.failure_count == 2
    
    def test_transitions_to_half_open(self):
        """Circuit should transition to HALF_OPEN after timeout"""
        breaker = CircuitBreaker(name="test", failure_threshold=3, timeout=1)
        
        # Open the circuit
        for _ in range(3):
            breaker.record_failure()
        
        assert breaker.state == CircuitState.OPEN
        
        # Simulate timeout by setting last_failure_time in past
        breaker.last_failure_time = datetime.utcnow() - timedelta(seconds=2)
        
        # Should transition to HALF_OPEN
        assert breaker.can_execute() is True
        assert breaker.state == CircuitState.HALF_OPEN
    
    def test_half_open_closes_on_success(self):
        """Circuit should CLOSE after enough successes in HALF_OPEN"""
        breaker = CircuitBreaker(
            name="test",
            failure_threshold=3,
            success_threshold=2,
            timeout=1
        )
        
        # Open the circuit
        for _ in range(3):
            breaker.record_failure()
        
        # Transition to HALF_OPEN
        breaker.last_failure_time = datetime.utcnow() - timedelta(seconds=2)
        breaker.can_execute()
        
        assert breaker.state == CircuitState.HALF_OPEN
        
        # Record successes
        breaker.record_success()
        assert breaker.state == CircuitState.HALF_OPEN
        
        breaker.record_success()
        assert breaker.state == CircuitState.CLOSED
        assert breaker.failure_count == 0
    
    def test_half_open_reopens_on_failure(self):
        """Circuit should reopen on any failure in HALF_OPEN state"""
        breaker = CircuitBreaker(name="test", failure_threshold=3, timeout=1)
        
        # Open the circuit
        for _ in range(3):
            breaker.record_failure()
        
        # Transition to HALF_OPEN
        breaker.last_failure_time = datetime.utcnow() - timedelta(seconds=2)
        breaker.can_execute()
        
        assert breaker.state == CircuitState.HALF_OPEN
        
        # Single failure should reopen
        breaker.record_failure()
        assert breaker.state == CircuitState.OPEN
    
    def test_blocked_when_open_before_timeout(self):
        """Execution should be blocked when OPEN and timeout not reached"""
        breaker = CircuitBreaker(name="test", failure_threshold=3, timeout=60)
        
        # Open the circuit
        for _ in range(3):
            breaker.record_failure()
        
        assert breaker.state == CircuitState.OPEN
        assert breaker.can_execute() is False


class TestCircuitBreakerEdgeCases:
    """Edge case tests for CircuitBreaker"""
    
    def test_multiple_quick_failures(self):
        """Should handle rapid consecutive failures"""
        breaker = CircuitBreaker(name="test", failure_threshold=5)
        
        for _ in range(10):
            breaker.record_failure()
        
        assert breaker.state == CircuitState.OPEN
        assert breaker.failure_count == 10
    
    def test_success_then_failures(self):
        """Success should reset failure count"""
        breaker = CircuitBreaker(name="test", failure_threshold=3)
        
        breaker.record_failure()
        breaker.record_failure()
        breaker.record_success()  # Reset
        
        assert breaker.failure_count == 0
        
        breaker.record_failure()
        breaker.record_failure()
        
        assert breaker.state == CircuitState.CLOSED
        assert breaker.failure_count == 2
    
    def test_different_threshold_configurations(self):
        """Test with different threshold configurations"""
        # Low threshold
        breaker1 = CircuitBreaker(name="low", failure_threshold=1)
        breaker1.record_failure()
        assert breaker1.state == CircuitState.OPEN
        
        # High threshold
        breaker2 = CircuitBreaker(name="high", failure_threshold=10)
        for _ in range(9):
            breaker2.record_failure()
        assert breaker2.state == CircuitState.CLOSED
        
        breaker2.record_failure()
        assert breaker2.state == CircuitState.OPEN
