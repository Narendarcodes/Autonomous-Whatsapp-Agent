"""
Tests for Retry Utilities
"""

import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock
from app.core.retry import retry_with_backoff, with_retry


class TestRetryWithBackoff:
    """Test cases for retry_with_backoff function"""
    
    @pytest.mark.asyncio
    async def test_success_no_retry(self):
        """Should return result immediately on success"""
        mock_func = AsyncMock(return_value="success")
        
        result = await retry_with_backoff(mock_func, max_retries=3)
        
        assert result == "success"
        assert mock_func.call_count == 1
    
    @pytest.mark.asyncio
    async def test_retry_on_failure_then_success(self):
        """Should retry on failure and return on eventual success"""
        call_count = 0
        
        async def flaky_func():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ConnectionError("Connection failed")
            return "success"
        
        result = await retry_with_backoff(
            flaky_func,
            max_retries=3,
            base_delay=0.01,  # Fast for testing
            retryable_exceptions=(ConnectionError,)
        )
        
        assert result == "success"
        assert call_count == 3
    
    @pytest.mark.asyncio
    async def test_exhausts_retries(self):
        """Should raise exception after exhausting all retries"""
        mock_func = AsyncMock(side_effect=ConnectionError("Always fails"))
        
        with pytest.raises(ConnectionError):
            await retry_with_backoff(
                mock_func,
                max_retries=2,
                base_delay=0.01,
                retryable_exceptions=(ConnectionError,)
            )
        
        assert mock_func.call_count == 3  # Initial + 2 retries
    
    @pytest.mark.asyncio
    async def test_non_retryable_exception_raises_immediately(self):
        """Should not retry non-retryable exceptions"""
        mock_func = AsyncMock(side_effect=ValueError("Not retryable"))
        
        with pytest.raises(ValueError):
            await retry_with_backoff(
                mock_func,
                max_retries=3,
                base_delay=0.01,
                retryable_exceptions=(ConnectionError,)  # ValueError not included
            )
        
        assert mock_func.call_count == 1
    
    @pytest.mark.asyncio
    async def test_passes_args_and_kwargs(self):
        """Should pass arguments correctly to the function"""
        async def func_with_args(a, b, c=None):
            return f"{a}-{b}-{c}"
        
        result = await retry_with_backoff(
            func_with_args,
            "arg1", "arg2",
            c="kwarg",
            max_retries=1
        )
        
        assert result == "arg1-arg2-kwarg"
    
    @pytest.mark.asyncio
    async def test_respects_max_delay(self):
        """Delay should not exceed max_delay"""
        call_times = []
        
        async def failing_func():
            call_times.append(asyncio.get_event_loop().time())
            raise ConnectionError("Fail")
        
        try:
            await retry_with_backoff(
                failing_func,
                max_retries=3,
                base_delay=10.0,
                max_delay=0.05,  # Cap at 50ms
                jitter=False,
                retryable_exceptions=(ConnectionError,)
            )
        except ConnectionError:
            pass
        
        # Check delays between calls are capped
        for i in range(1, len(call_times)):
            delay = call_times[i] - call_times[i-1]
            assert delay < 0.1  # Should be around max_delay


class TestWithRetryDecorator:
    """Test cases for with_retry decorator"""
    
    @pytest.mark.asyncio
    async def test_decorator_success(self):
        """Decorated function should work normally on success"""
        @with_retry(max_retries=2, base_delay=0.01)
        async def successful_func():
            return "decorated success"
        
        result = await successful_func()
        assert result == "decorated success"
    
    @pytest.mark.asyncio
    async def test_decorator_retries(self):
        """Decorated function should retry on failure"""
        call_count = 0
        
        @with_retry(
            max_retries=2,
            base_delay=0.01,
            retryable_exceptions=(RuntimeError,)
        )
        async def flaky_decorated():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise RuntimeError("Retry me")
            return "eventually works"
        
        result = await flaky_decorated()
        assert result == "eventually works"
        assert call_count == 2
    
    @pytest.mark.asyncio
    async def test_decorator_with_arguments(self):
        """Decorated function should handle arguments"""
        @with_retry(max_retries=1, base_delay=0.01)
        async def func_with_args(x, y, z=10):
            return x + y + z
        
        result = await func_with_args(1, 2, z=3)
        assert result == 6


class TestRetryEdgeCases:
    """Edge case tests for retry functionality"""
    
    @pytest.mark.asyncio
    async def test_zero_retries(self):
        """Should work with zero retries (single attempt)"""
        mock_func = AsyncMock(return_value="single try")
        
        result = await retry_with_backoff(mock_func, max_retries=0)
        
        assert result == "single try"
        assert mock_func.call_count == 1
    
    @pytest.mark.asyncio
    async def test_zero_retries_failure(self):
        """Should fail immediately with zero retries"""
        mock_func = AsyncMock(side_effect=RuntimeError("Fail"))
        
        with pytest.raises(RuntimeError):
            await retry_with_backoff(
                mock_func,
                max_retries=0,
                retryable_exceptions=(RuntimeError,)
            )
        
        assert mock_func.call_count == 1
    
    @pytest.mark.asyncio
    async def test_multiple_exception_types(self):
        """Should retry on multiple exception types"""
        exceptions = [ConnectionError("1"), TimeoutError("2"), "success"]
        call_count = 0
        
        async def multi_fail():
            nonlocal call_count
            result = exceptions[call_count]
            call_count += 1
            if isinstance(result, Exception):
                raise result
            return result
        
        result = await retry_with_backoff(
            multi_fail,
            max_retries=3,
            base_delay=0.01,
            retryable_exceptions=(ConnectionError, TimeoutError)
        )
        
        assert result == "success"
        assert call_count == 3
