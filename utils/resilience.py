"""
Resilience patterns for robust API handling.
"""
import time
import logging
import threading
from enum import Enum
from typing import Callable, Any, Optional, Dict
from functools import wraps

logger = logging.getLogger(__name__)


class APIHealth(Enum):
    """API health status"""
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    DOWN = "down"


class HealthTracker:
    """Track API health metrics over time"""

    def __init__(self, name: str, window_seconds: int = 300):
        self.name = name
        self.window_seconds = window_seconds
        self._successes = []
        self._failures = []
        self._lock = threading.Lock()
        self._total_calls = 0
        self._total_errors = 0
        self._last_error: Optional[str] = None
        self._last_error_time: Optional[float] = None

    def record_success(self):
        """Record successful call"""
        with self._lock:
            now = time.time()
            self._successes.append(now)
            self._total_calls += 1
            # Clean old entries
            cutoff = now - self.window_seconds
            self._successes = [t for t in self._successes if t > cutoff]

    def record_failure(self, error_msg: str = ""):
        """Record failed call"""
        with self._lock:
            now = time.time()
            self._failures.append(now)
            self._total_calls += 1
            self._total_errors += 1
            self._last_error = error_msg
            self._last_error_time = now
            # Clean old entries
            cutoff = now - self.window_seconds
            self._failures = [t for t in self._failures if t > cutoff]

    def get_status(self) -> APIHealth:
        """Get current health status"""
        with self._lock:
            now = time.time()
            cutoff = now - self.window_seconds

            recent_successes = [t for t in self._successes if t > cutoff]
            recent_failures = [t for t in self._failures if t > cutoff]
            total_recent = len(recent_successes) + len(recent_failures)

            if total_recent == 0:
                return APIHealth.HEALTHY

            error_rate = len(recent_failures) / total_recent

            if error_rate >= 0.5:
                return APIHealth.DOWN
            elif error_rate >= 0.2:
                return APIHealth.UNHEALTHY
            elif error_rate >= 0.1:
                return APIHealth.DEGRADED
            return APIHealth.HEALTHY

    def get_stats(self) -> Dict[str, Any]:
        """Get health statistics"""
        with self._lock:
            return {
                'name': self.name,
                'status': self.get_status().value,
                'total_calls': self._total_calls,
                'total_errors': self._total_errors,
                'error_rate': self._total_errors / self._total_calls if self._total_calls > 0 else 0,
                'last_error': self._last_error,
                'last_error_time': self._last_error_time,
            }


class TimeoutHandler:
    """Handle operations with timeout"""

    @staticmethod
    def with_timeout(func: Callable, timeout: float, default=None, args=(), kwargs=None):
        """
        Execute function with timeout.
        Returns default if timeout exceeded.
        """
        import queue
        import threading

        kwargs = kwargs or {}
        result_queue = queue.Queue()

        def target():
            try:
                result_queue.put(('success', func(*args, **kwargs)))
            except Exception as e:
                result_queue.put(('error', e))

        t = threading.Thread(target=target)
        t.daemon = True
        t.start()
        t.join(timeout)

        if t.is_alive():
            # Timeout - function still running
            logger.warning(f"Function {func.__name__} timed out after {timeout}s")
            return default

        if result_queue.empty():
            return default

        status, result = result_queue.get_nowait()
        if status == 'success':
            return result
        else:
            raise result


class Bulkhead:
    """Semaphore-based bulkhead to limit concurrent calls to a resource"""

    def __init__(self, max_concurrent: int = 10, max_queued: int = 20):
        self._semaphore = threading.Semaphore(max_concurrent)
        self._max_queued = max_queued
        self._active = 0
        self._queued = 0
        self._lock = threading.Lock()
        self._rejected = 0

    def execute(self, func: Callable, *args, **kwargs) -> Any:
        """Execute function with bulkhead limit"""
        with self._lock:
            if self._queued >= self._max_queued:
                self._rejected += 1
                raise BulkheadFullError(f"Bulkhead queue full ({self._max_queued}), rejected {self._rejected} calls")

        try:
            acquired = self._semaphore.acquire(timeout=5)
            if not acquired:
                with self._lock:
                    self._rejected += 1
                raise BulkheadFullError("Bulkhead timeout - could not acquire semaphore")

            with self._lock:
                self._active += 1

            try:
                return func(*args, **kwargs)
            finally:
                with self._lock:
                    self._active -= 1
                self._semaphore.release()
        except Exception as e:
            raise e

    def get_stats(self) -> Dict[str, int]:
        """Get bulkhead stats"""
        with self._lock:
            return {
                'active': self._active,
                'rejected': self._rejected,
                'queued': self._queued,
            }


class BulkheadFullError(Exception):
    """Raised when bulkhead is full and can't accept more requests"""
    pass


# Global health trackers
_health_trackers: Dict[str, HealthTracker] = {}


def get_health_tracker(name: str) -> HealthTracker:
    """Get or create health tracker for an API"""
    if name not in _health_trackers:
        _health_trackers[name] = HealthTracker(name)
    return _health_trackers[name]


def get_all_health() -> Dict[str, Dict[str, Any]]:
    """Get health status of all tracked APIs"""
    return {name: tracker.get_stats() for name, tracker in _health_trackers.items()}


def resilient_call(api_name: str, retries: int = 3, base_delay: float = 0.5,
                   max_delay: float = 30, timeout: float = 10):
    """
    Decorator for making resilient API calls.

    Features:
    - Automatic retry with exponential backoff
    - Health tracking
    - Timeout handling
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            tracker = get_health_tracker(api_name)
            last_error = None

            for attempt in range(retries):
                try:
                    # Timeout wrapper for each attempt
                    result = TimeoutHandler.with_timeout(
                        func, timeout, default=None, args=args, kwargs=kwargs
                    )

                    if result is None:
                        raise TimeoutError(f"API call timed out after {timeout}s")

                    tracker.record_success()
                    return result

                except Exception as e:
                    last_error = str(e)
                    tracker.record_failure(last_error)

                    if attempt < retries - 1:
                        # Exponential backoff with jitter
                        delay = min(base_delay * (2 ** attempt), max_delay)
                        delay = delay * (0.5 + (hash(last_error) % 100) / 200)  # Add jitter
                        logger.warning(f"[{api_name}] Attempt {attempt + 1} failed: {last_error}. "
                                      f"Retrying in {delay:.1f}s...")
                        time.sleep(delay)
                    else:
                        logger.error(f"[{api_name}] All {retries} attempts failed: {last_error}")

            return None

        return wrapper
    return decorator
