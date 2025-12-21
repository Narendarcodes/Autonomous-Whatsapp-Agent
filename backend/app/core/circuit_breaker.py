"""
Circuit Breaker Pattern Implementation
Prevents cascading failures by stopping calls to failing services
"""

import asyncio
from datetime import datetime, timedelta
from typing import Optional, Callable, Any
from enum import Enum
from dataclasses import dataclass, field

from app.core.logging import logger


class CircuitState(Enum):
    """Circuit breaker states"""
    CLOSED = "closed"      # Normal operation, requests pass through
    OPEN = "open"          # Failing, requests blocked
    HALF_OPEN = "half_open"  # Testing if service recovered


@dataclass
class CircuitBreaker:
    """
    Circuit breaker for protecting against cascading failures
    
    Usage:
        breaker = CircuitBreaker(name="calendar_api", failure_threshold=5)
        
        if breaker.can_execute():
            try:
                result = await some_api_call()
                breaker.record_success()
            except Exception as e:
                breaker.record_failure()
                raise
        else:
            raise CircuitOpenError("Service unavailable")
    """
    name: str
    failure_threshold: int = 5  # Failures before opening circuit
    success_threshold: int = 2  # Successes in half-open before closing
    timeout: int = 30  # Seconds before trying half-open
    
    # State tracking
    state: CircuitState = field(default=CircuitState.CLOSED)
    failure_count: int = field(default=0)
    success_count: int = field(default=0)
    last_failure_time: Optional[datetime] = field(default=None)
    
    def can_execute(self) -> bool:
        """Check if a request can be executed"""
        if self.state == CircuitState.CLOSED:
            return True
        
        if self.state == CircuitState.OPEN:
            # Check if timeout has passed
            if self.last_failure_time and \
               datetime.utcnow() - self.last_failure_time > timedelta(seconds=self.timeout):
                # Transition to half-open
                self.state = CircuitState.HALF_OPEN
                self.success_count = 0
                logger.info(f"Circuit breaker '{self.name}' transitioning to HALF_OPEN")
                return True
            return False
        
        # Half-open: allow limited requests
        return True
    
    def record_success(self) -> None:
        """Record a successful call"""
        if self.state == CircuitState.HALF_OPEN:
            self.success_count += 1
            if self.success_count >= self.success_threshold:
                self._close_circuit()
        elif self.state == CircuitState.CLOSED:
            # Reset failure count on success
            self.failure_count = 0
    
    def record_failure(self) -> None:
        """Record a failed call"""
        self.failure_count += 1
        self.last_failure_time = datetime.utcnow()
        
        if self.state == CircuitState.HALF_OPEN:
            # Any failure in half-open opens the circuit
            self._open_circuit()
        elif self.state == CircuitState.CLOSED:
            if self.failure_count >= self.failure_threshold:
                self._open_circuit()
    
    def _open_circuit(self) -> None:
        """Open the circuit"""
        self.state = CircuitState.OPEN
        logger.warning(f"Circuit breaker '{self.name}' OPENED after {self.failure_count} failures")
    
    def _close_circuit(self) -> None:
        """Close the circuit"""
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.success_count = 0
        logger.info(f"Circuit breaker '{self.name}' CLOSED - service recovered")
    
    def get_state(self) -> dict:
        """Get current state for monitoring"""
        return {
            "name": self.name,
            "state": self.state.value,
            "failure_count": self.failure_count,
            "success_count": self.success_count,
            "last_failure": self.last_failure_time.isoformat() if self.last_failure_time else None
        }


class CircuitOpenError(Exception):
    """Raised when circuit is open and request cannot proceed"""
    pass


# Global circuit breakers for different services
circuit_breakers = {
    "calendar_api": CircuitBreaker(name="calendar_api", failure_threshold=5, timeout=30),
    "llm_service": CircuitBreaker(name="llm_service", failure_threshold=3, timeout=60),
    "whatsapp_api": CircuitBreaker(name="whatsapp_api", failure_threshold=5, timeout=30),
}


def get_circuit_breaker(name: str) -> CircuitBreaker:
    """Get or create a circuit breaker by name"""
    if name not in circuit_breakers:
        circuit_breakers[name] = CircuitBreaker(name=name)
    return circuit_breakers[name]


async def with_circuit_breaker(
    breaker_name: str,
    func: Callable,
    *args,
    fallback: Any = None,
    **kwargs
) -> Any:
    """
    Execute a function with circuit breaker protection
    
    Args:
        breaker_name: Name of the circuit breaker to use
        func: Async function to execute
        *args: Positional arguments for the function
        fallback: Value to return if circuit is open
        **kwargs: Keyword arguments for the function
        
    Returns:
        Function result or fallback value
        
    Raises:
        CircuitOpenError if circuit is open and no fallback provided
    """
    breaker = get_circuit_breaker(breaker_name)
    
    if not breaker.can_execute():
        if fallback is not None:
            logger.warning(f"Circuit '{breaker_name}' is open, returning fallback")
            return fallback
        raise CircuitOpenError(f"Circuit '{breaker_name}' is open - service unavailable")
    
    try:
        result = await func(*args, **kwargs)
        breaker.record_success()
        return result
    except Exception as e:
        breaker.record_failure()
        raise


def get_all_circuit_states() -> dict:
    """Get state of all circuit breakers for monitoring"""
    return {name: breaker.get_state() for name, breaker in circuit_breakers.items()}
