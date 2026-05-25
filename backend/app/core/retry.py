"""
Retry Utilities
Implements retry with exponential backoff for transient failures
"""

import asyncio
import random
from typing import Callable, TypeVar, Any, Optional, Tuple, Type
from functools import wraps

from app.core.logging import logger


T = TypeVar('T')


async def retry_with_backoff(
    func: Callable[..., T],
    *args,
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    exponential_base: float = 2.0,
    jitter: bool = True,
    retryable_exceptions: Tuple[Type[Exception], ...] = (Exception,),
    **kwargs
) -> T:
    """
    Execute an async function with exponential backoff retry
    
    Args:
        func: Async function to execute
        *args: Positional arguments for the function
        max_retries: Maximum number of retry attempts
        base_delay: Initial delay in seconds
        max_delay: Maximum delay in seconds
        exponential_base: Base for exponential backoff
        jitter: Whether to add random jitter to delays
        retryable_exceptions: Tuple of exceptions that should trigger retry
        **kwargs: Keyword arguments for the function
        
    Returns:
        Result of the function
        
    Raises:
        Last exception if all retries fail
    """
    last_exception = None
    
    for attempt in range(max_retries + 1):
        try:
            return await func(*args, **kwargs)
        except retryable_exceptions as e:
            last_exception = e
            
            if attempt == max_retries:
                logger.error(f"All {max_retries + 1} attempts failed for {func.__name__}: {e}")
                raise
            
            # Calculate delay with exponential backoff
            delay = min(base_delay * (exponential_base ** attempt), max_delay)
            
            # Add jitter to prevent thundering herd
            if jitter:
                delay = delay * (0.5 + random.random())
            
            logger.warning(
                f"Attempt {attempt + 1}/{max_retries + 1} failed for {func.__name__}: {e}. "
                f"Retrying in {delay:.2f}s..."
            )
            
            await asyncio.sleep(delay)
    
    # Should never reach here, but just in case
    raise last_exception


def with_retry(
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    exponential_base: float = 2.0,
    jitter: bool = True,
    retryable_exceptions: Tuple[Type[Exception], ...] = (Exception,)
):
    """
    Decorator for adding retry with exponential backoff to async functions
    
    Usage:
        @with_retry(max_retries=3, base_delay=1.0)
        async def my_api_call():
            ...
    """
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @wraps(func)
        async def wrapper(*args, **kwargs) -> T:
            return await retry_with_backoff(
                func,
                *args,
                max_retries=max_retries,
                base_delay=base_delay,
                max_delay=max_delay,
                exponential_base=exponential_base,
                jitter=jitter,
                retryable_exceptions=retryable_exceptions,
                **kwargs
            )
        return wrapper
    return decorator


# Common retryable exceptions for HTTP calls
HTTP_RETRYABLE_EXCEPTIONS = (
    ConnectionError,
    TimeoutError,
    asyncio.TimeoutError,
)

# Common retryable exceptions for database operations
DB_RETRYABLE_EXCEPTIONS = (
    ConnectionError,
    TimeoutError,
)
