"""
Rate limiting and circuit breaker patterns for API protection.
"""
import time
import random
import asyncio
import logging
from enum import Enum
from collections import deque
import threading
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)


class APIState(Enum):
    """API Circuit Breaker states"""
    CLOSED = "closed"      # Normal operation
    OPEN = "open"          # Failing, reject requests
    HALF_OPEN = "half_open"  # Testing if API recovered


class RateLimiter:
    """Token bucket rate limiter for API calls with stats tracking"""

    def __init__(self, max_calls=10, period=60, name="default"):
        self.max_calls = max_calls
        self.period = period
        self.calls = deque()
        self.lock = threading.Lock()
        self.name = name
        self._total_calls = 0
        self._total_waits = 0
        self._total_rejected = 0

    def can_call(self):
        """Check if we can make a call without waiting"""
        with self.lock:
            now = time.time()
            while self.calls and self.calls[0] < now - self.period:
                self.calls.popleft()
            return len(self.calls) < self.max_calls

    def wait_if_needed(self):
        """Wait until a call can be made - interruptible by shutdown signals"""
        with self.lock:
            now = time.time()
            while self.calls and self.calls[0] < now - self.period:
                self.calls.popleft()

            if len(self.calls) >= self.max_calls:
                wait_time = self.calls[0] + self.period - now
                if wait_time > 0:
                    self._total_waits += 1
                    # Interruptible sleep - allow graceful shutdown
                    slept = 0.0
                    while slept < wait_time:
                        chunk = min(0.5, wait_time - slept)
                        try:
                            time.sleep(chunk)
                        except (KeyboardInterrupt, SystemExit):
                            # Re-raise but allow cleanup
                            raise
                        slept += chunk
                    now = time.time()
                    while self.calls and self.calls[0] < now - self.period:
                        self.calls.popleft()

            self.calls.append(time.time())
            self._total_calls += 1

    def record_call(self):
        """Record a successful call"""
        with self.lock:
            self.calls.append(time.time())
            self._total_calls += 1

    def get_stats(self) -> Dict[str, Any]:
        """Get rate limiter stats"""
        with self.lock:
            return {
                'name': self.name,
                'max_calls': self.max_calls,
                'period': self.period,
                'current_calls': len(self.calls),
                'total_calls': self._total_calls,
                'total_waits': self._total_waits,
                'total_rejected': self._total_rejected,
            }


class CircuitBreaker:
    """Circuit breaker pattern with detailed state tracking"""

    def __init__(self, failure_threshold=5, recovery_timeout=15, half_open_max=2, name="default"):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.half_open_max = half_open_max
        self.name = name

        self.state = APIState.CLOSED
        self.failure_count = 0
        self.success_count = 0
        self.last_failure_time = None
        self.last_success_time = None
        self.half_open_calls = 0
        self.lock = threading.Lock()
        self._total_opens = 0
        self._total_closes = 0

    def can_execute(self):
        """Check if request can proceed"""
        with self.lock:
            now = time.time()

            if self.state == APIState.CLOSED:
                return True

            elif self.state == APIState.OPEN:
                if self.last_failure_time and (now - self.last_failure_time) >= self.recovery_timeout:
                    self.state = APIState.HALF_OPEN
                    self.half_open_calls = 0
                    logger.info(f"[{self.name}] Circuit breaker entering HALF_OPEN state")
                    return True
                return False

            elif self.state == APIState.HALF_OPEN:
                if self.half_open_calls < self.half_open_max:
                    self.half_open_calls += 1
                    return True
                return False

            return False

    def record_success(self):
        """Record successful API call"""
        with self.lock:
            self.success_count += 1
            self.last_success_time = time.time()

            if self.state == APIState.HALF_OPEN:
                self.state = APIState.CLOSED
                self.failure_count = 0
                self._total_closes += 1
                logger.info(f"[{self.name}] Circuit breaker CLOSED (recovered)")

    def record_failure(self, error_msg: str = ""):
        """Record failed API call"""
        with self.lock:
            self.failure_count += 1
            self.last_failure_time = time.time()

            if self.state == APIState.HALF_OPEN:
                self.state = APIState.OPEN
                logger.warning(f"[{self.name}] Circuit breaker OPEN after half_open failure")
            elif self.failure_count >= self.failure_threshold:
                self.state = APIState.OPEN
                self._total_opens += 1
                logger.warning(f"[{self.name}] Circuit breaker OPEN after {self.failure_count} failures: {error_msg}")

    def get_status(self) -> str:
        """Get current state as string"""
        return self.state.value.upper()

    def get_stats(self) -> Dict[str, Any]:
        """Get circuit breaker stats"""
        with self.lock:
            return {
                'name': self.name,
                'state': self.state.value,
                'failure_count': self.failure_count,
                'success_count': self.success_count,
                'failure_threshold': self.failure_threshold,
                'recovery_timeout': self.recovery_timeout,
                'total_opens': self._total_opens,
                'total_closes': self._total_closes,
                'last_failure': self.last_failure_time,
                'last_success': self.last_success_time,
            }


class AsyncRateLimiter:
    """Simple async rate limiter - just controls concurrency, no token bucket"""

    def __init__(self, concurrency=15):
        self.semaphore = asyncio.Semaphore(concurrency)

    async def __aenter__(self):
        await self.semaphore.acquire()
        return self

    async def __aexit__(self, *args):
        self.semaphore.release()
        return False


def exponential_backoff(attempt, base_delay=1.0, max_delay=32.0, jitter=True):
    """Calculate delay with exponential backoff and optional jitter"""
    delay = min(base_delay * (2 ** attempt), max_delay)
    if jitter:
        delay = delay * (0.5 + random.random())  # 50-150% of delay
    return delay


# Global rate limiters for each API
_coingecko_limiter = RateLimiter(max_calls=5, period=60, name="coingecko")  # Conservative: 5 calls/min
_yahoo_limiter = RateLimiter(max_calls=30, period=60, name="yahoo")
_circuit_breakers: Dict[str, CircuitBreaker] = {
    # CoinGecko: very slow recovery since free tier is very limited
    'coingecko': CircuitBreaker(failure_threshold=10, recovery_timeout=300, name="coingecko"),
    # Yahoo: shared between crypto and stock services
    'yahoo': CircuitBreaker(failure_threshold=10, recovery_timeout=30, name="yahoo"),
    # Yahoo stock: separate breaker so crypto scanner doesn't block stock fetches
    'yahoo_stock': CircuitBreaker(failure_threshold=30, recovery_timeout=60, name="yahoo_stock"),
    'tradingview': CircuitBreaker(failure_threshold=10, recovery_timeout=30, name="tradingview"),
    'finnhub': CircuitBreaker(failure_threshold=10, recovery_timeout=60, name="finnhub"),
}


def reset_all_circuit_breakers():
    """Reset all circuit breakers to CLOSED state"""
    for name, breaker in _circuit_breakers.items():
        breaker.state = APIState.CLOSED
        breaker.failure_count = 0
        breaker.half_open_calls = 0
        logger.info(f"[{name}] Circuit breaker RESET to CLOSED")
    return True


def get_circuit_breaker_status() -> Dict[str, str]:
    """Get status of all circuit breakers"""
    return {name: breaker.get_status() for name, breaker in _circuit_breakers.items()}

# Global async rate limiters
_async_coingecko_limiter = AsyncRateLimiter(concurrency=5)
_async_yahoo_limiter = AsyncRateLimiter(concurrency=10)


def get_all_api_stats() -> Dict[str, Dict[str, Any]]:
    """Get stats for all APIs"""
    stats = {
        'coingecko': {
            'limiter': _coingecko_limiter.get_stats(),
            'breaker': _circuit_breakers['coingecko'].get_stats(),
        },
        'yahoo': {
            'limiter': _yahoo_limiter.get_stats(),
            'breaker': _circuit_breakers['yahoo'].get_stats(),
        },
        'tradingview': {
            'breaker': _circuit_breakers['tradingview'].get_stats(),
        },
        'finnhub': {
            'breaker': _circuit_breakers['finnhub'].get_stats(),
        },
    }
    return stats

