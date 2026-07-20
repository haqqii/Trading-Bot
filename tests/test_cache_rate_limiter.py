"""
Unit tests for utils/cache.py and utils/rate_limiter.py
Tests caching and rate limiting infrastructure.
"""
import sys
import os
import time
import threading

import pytest

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.cache import Cache
from utils.rate_limiter import (
    RateLimiter,
    CircuitBreaker,
    APIState,
    AsyncRateLimiter,
    exponential_backoff,
)


# === Cache Tests ===

class TestCacheBasic:
    """Basic tests for Cache class."""

    def test_set_and_get(self):
        """Basic set/get should work."""
        cache = Cache(default_ttl=60)
        cache.set('key1', 'value1')
        assert cache.get('key1') == 'value1'

    def test_get_missing_key(self):
        """Missing key should return None."""
        cache = Cache(default_ttl=60)
        assert cache.get('nonexistent') is None

    def test_default_ttl(self):
        """Cache should use default TTL when not specified."""
        cache = Cache(default_ttl=60)
        cache.set('key1', 'value1')
        assert cache.get('key1') == 'value1'

    def test_custom_ttl(self):
        """Custom TTL should be respected."""
        cache = Cache(default_ttl=60)
        cache.set('key1', 'value1', ttl=1)
        time.sleep(1.1)
        assert cache.get('key1') is None

    def test_delete(self):
        """Delete should remove key."""
        cache = Cache(default_ttl=60)
        cache.set('key1', 'value1')
        cache.delete('key1')
        assert cache.get('key1') is None

    def test_clear(self):
        """Clear should remove all entries."""
        cache = Cache(default_ttl=60)
        cache.set('key1', 'value1')
        cache.set('key2', 'value2')
        cache.clear()
        assert cache.get('key1') is None
        assert cache.get('key2') is None


class TestCacheStale:
    """Tests for stale cache (fallback) functionality."""

    def test_stale_cache_set_and_get(self):
        """Stale cache should store and return values."""
        cache = Cache(default_ttl=60, stale_ttl=120)
        cache.set_stale('key1', 'value1')
        assert cache.get_stale('key1') == 'value1'

    def test_stale_cache_ttl(self):
        """Stale cache should expire after stale_ttl."""
        cache = Cache(default_ttl=60, stale_ttl=1)
        cache.set_stale('key1', 'value1')
        time.sleep(1.1)
        assert cache.get_stale('key1') is None

    def test_expired_moves_to_stale(self):
        """Expired fresh cache should move to stale cache."""
        cache = Cache(default_ttl=1, stale_ttl=120)
        cache.set('key1', 'value1')
        time.sleep(1.1)
        # Fresh should be expired
        assert cache.get('key1') is None
        # But stale should still have it
        assert cache.get_stale('key1') == 'value1'


class TestCacheGetOrFetch:
    """Tests for get_or_fetch method."""

    def test_get_or_fetch_cache_hit(self):
        """Cache hit should not call fetch function."""
        cache = Cache(default_ttl=60)
        cache.set('key1', 'cached_value')

        called = []
        def fetch_func():
            called.append(True)
            return 'fetched_value', False

        value, is_stale = cache.get_or_fetch('key1', fetch_func)
        assert value == 'cached_value'
        assert is_stale is False
        assert len(called) == 0

    def test_get_or_fetch_cache_miss_returns_none(self):
        """Cache miss with no stale data should return (None, False).

        Note: The current implementation does NOT actually call fetch_func.
        This is a known limitation. The fetch_func parameter is accepted
        but not invoked - callers should handle fetching themselves.
        """
        cache = Cache(default_ttl=60)

        def fetch_func():
            return 'fetched_value', False

        value, is_stale = cache.get_or_fetch('key1', fetch_func)
        # Without fetch_func being called, we get None for cache miss
        assert value is None
        assert is_stale is False


class TestCacheStats:
    """Tests for cache statistics."""

    def test_stats_initial(self):
        """Initial stats should be zero."""
        cache = Cache(default_ttl=60)
        stats = cache.stats()
        assert stats['hits'] == 0
        assert stats['misses'] == 0
        assert stats['sets'] == 0

    def test_stats_track_hits(self):
        """Stats should track hits."""
        cache = Cache(default_ttl=60)
        cache.set('key1', 'value1')
        cache.get('key1')
        stats = cache.stats()
        assert stats['hits'] == 1

    def test_stats_track_misses(self):
        """Stats should track misses."""
        cache = Cache(default_ttl=60)
        cache.get('nonexistent')
        stats = cache.stats()
        assert stats['misses'] == 1

    def test_stats_track_sets(self):
        """Stats should track sets."""
        cache = Cache(default_ttl=60)
        cache.set('key1', 'value1')
        cache.set('key2', 'value2')
        stats = cache.stats()
        assert stats['sets'] == 2

    def test_hit_rate_calculation(self):
        """Hit rate should be calculated correctly."""
        cache = Cache(default_ttl=60)
        cache.set('key1', 'value1')
        cache.get('key1')  # hit
        cache.get('key1')  # hit
        cache.get('nonexistent')  # miss
        rate = cache.hit_rate()
        assert abs(rate - 2/3) < 0.01


class TestCacheError:
    """Tests for cache error tracking."""

    def test_set_error(self):
        """set_error should record error."""
        cache = Cache(default_ttl=60)
        cache.set_error('key1', 'API failed')
        assert cache.get_last_error('key1') == 'API failed'

    def test_get_last_error_missing(self):
        """Missing error should return None."""
        cache = Cache(default_ttl=60)
        assert cache.get_last_error('nonexistent') is None

    def test_set_clears_error(self):
        """Setting value should clear previous error."""
        cache = Cache(default_ttl=60)
        cache.set_error('key1', 'API failed')
        cache.set('key1', 'value1')
        assert cache.get_last_error('key1') is None


class TestCacheCleanup:
    """Tests for cache cleanup."""

    def test_cleanup_removes_expired(self):
        """Cleanup should remove expired entries."""
        cache = Cache(default_ttl=1, stale_ttl=120)
        cache.set('key1', 'value1')
        time.sleep(1.1)
        cache.cleanup()
        # key1 should now be in stale cache
        assert cache.get('key1') is None
        assert cache.get_stale('key1') == 'value1'


# === RateLimiter Tests ===

class TestRateLimiterBasic:
    """Basic tests for RateLimiter."""

    def test_initial_can_call(self):
        """Should be able to call initially."""
        limiter = RateLimiter(max_calls=10, period=60)
        assert limiter.can_call() is True

    def test_record_call(self):
        """Recording calls should update counter."""
        limiter = RateLimiter(max_calls=10, period=60)
        limiter.record_call()
        limiter.record_call()
        stats = limiter.get_stats()
        assert stats['total_calls'] == 2

    def test_max_calls_limit(self):
        """Should reject calls when at max."""
        limiter = RateLimiter(max_calls=3, period=60)
        limiter.record_call()
        limiter.record_call()
        limiter.record_call()
        assert limiter.can_call() is False

    def test_max_calls_limit_with_call(self):
        """record_call after limit should still work."""
        limiter = RateLimiter(max_calls=3, period=60)
        for _ in range(3):
            limiter.record_call()
        # 4th call - should still be added
        limiter.record_call()
        stats = limiter.get_stats()
        assert stats['total_calls'] == 4

    def test_window_expiry(self):
        """Calls outside window should not count."""
        limiter = RateLimiter(max_calls=2, period=1)  # 1 second window
        limiter.record_call()
        limiter.record_call()
        # At limit
        assert limiter.can_call() is False
        # Wait for window to expire
        time.sleep(1.1)
        # Now can call again
        assert limiter.can_call() is True


class TestRateLimiterWait:
    """Tests for wait_if_needed method."""

    def test_wait_no_calls(self):
        """Should not wait when no calls."""
        limiter = RateLimiter(max_calls=10, period=60)
        start = time.time()
        limiter.wait_if_needed()
        elapsed = time.time() - start
        # Should be near-instant
        assert elapsed < 0.1


class TestRateLimiterStats:
    """Tests for rate limiter stats."""

    def test_stats_initial(self):
        """Initial stats should be zero."""
        limiter = RateLimiter(max_calls=10, period=60)
        stats = limiter.get_stats()
        assert stats['total_calls'] == 0
        assert stats['total_waits'] == 0

    def test_stats_have_required_fields(self):
        """Stats should have required fields."""
        limiter = RateLimiter(max_calls=10, period=60)
        stats = limiter.get_stats()
        assert 'name' in stats
        assert 'max_calls' in stats
        assert 'period' in stats
        assert 'current_calls' in stats
        assert 'total_calls' in stats


# === CircuitBreaker Tests ===

class TestCircuitBreakerBasic:
    """Basic tests for CircuitBreaker."""

    def test_initial_closed(self):
        """Circuit should start closed."""
        breaker = CircuitBreaker()
        assert breaker.state == APIState.CLOSED
        assert breaker.can_execute() is True

    def test_record_success(self):
        """Success should not change state."""
        breaker = CircuitBreaker()
        breaker.record_success()
        assert breaker.state == APIState.CLOSED

    def test_failures_open_breaker(self):
        """Enough failures should open breaker."""
        breaker = CircuitBreaker(failure_threshold=3)
        breaker.record_failure()
        breaker.record_failure()
        assert breaker.state == APIState.CLOSED
        breaker.record_failure()
        assert breaker.state == APIState.OPEN
        assert breaker.can_execute() is False

    def test_open_blocks_execution(self):
        """Open breaker should block execution."""
        breaker = CircuitBreaker(failure_threshold=2)
        breaker.record_failure()
        breaker.record_failure()
        assert breaker.state == APIState.OPEN
        assert breaker.can_execute() is False

    def test_recovery_timeout_to_half_open(self):
        """After recovery timeout, breaker should go to half-open."""
        breaker = CircuitBreaker(failure_threshold=2, recovery_timeout=1)
        breaker.record_failure()
        breaker.record_failure()
        assert breaker.state == APIState.OPEN
        time.sleep(1.1)
        assert breaker.can_execute() is True
        assert breaker.state == APIState.HALF_OPEN

    def test_half_open_success_closes(self):
        """Success in half-open should close breaker."""
        breaker = CircuitBreaker(failure_threshold=2, recovery_timeout=1)
        breaker.record_failure()
        breaker.record_failure()
        time.sleep(1.1)
        breaker.can_execute()  # Triggers transition to HALF_OPEN
        breaker.record_success()
        assert breaker.state == APIState.CLOSED

    def test_half_open_failure_reopens(self):
        """Failure in half-open should reopen breaker."""
        breaker = CircuitBreaker(failure_threshold=2, recovery_timeout=1)
        breaker.record_failure()
        breaker.record_failure()
        time.sleep(1.1)
        breaker.can_execute()  # HALF_OPEN
        breaker.record_failure()  # Failure
        assert breaker.state == APIState.OPEN


class TestCircuitBreakerStats:
    """Tests for CircuitBreaker stats."""

    def test_status_string(self):
        """Status should return readable string."""
        breaker = CircuitBreaker(failure_threshold=3)
        assert breaker.get_status() == 'CLOSED'
        breaker.record_failure()
        breaker.record_failure()
        breaker.record_failure()
        # 3 failures = OPEN
        assert breaker.get_status() == 'OPEN'

    def test_stats_have_required_fields(self):
        """Stats should have required fields."""
        breaker = CircuitBreaker()
        stats = breaker.get_stats()
        assert 'name' in stats
        assert 'state' in stats
        assert 'failure_count' in stats
        assert 'success_count' in stats
        assert 'failure_threshold' in stats


# === ExponentialBackoff Tests ===

class TestExponentialBackoff:
    """Tests for exponential_backoff function."""

    def test_first_attempt(self):
        """First attempt should be base_delay."""
        delay = exponential_backoff(0, base_delay=1.0, max_delay=32.0, jitter=False)
        assert delay == 1.0

    def test_second_attempt(self):
        """Second attempt should be 2x base_delay."""
        delay = exponential_backoff(1, base_delay=1.0, max_delay=32.0, jitter=False)
        assert delay == 2.0

    def test_third_attempt(self):
        """Third attempt should be 4x base_delay."""
        delay = exponential_backoff(2, base_delay=1.0, max_delay=32.0, jitter=False)
        assert delay == 4.0

    def test_max_delay_cap(self):
        """Should cap at max_delay."""
        delay = exponential_backoff(10, base_delay=1.0, max_delay=10.0, jitter=False)
        assert delay == 10.0

    def test_jitter_within_range(self):
        """Jitter should be within 50-150% of delay."""
        for attempt in range(5):
            delay = exponential_backoff(attempt, base_delay=1.0, max_delay=100.0, jitter=True)
            expected = min(1.0 * (2 ** attempt), 100.0)
            assert 0.5 * expected <= delay <= 1.5 * expected

    def test_no_jitter_exact(self):
        """Without jitter, delay should be exact."""
        for attempt in range(5):
            delay = exponential_backoff(attempt, base_delay=1.0, max_delay=100.0, jitter=False)
            expected = 1.0 * (2 ** attempt)
            assert delay == expected


# === AsyncRateLimiter Tests ===

class TestAsyncRateLimiter:
    """Tests for AsyncRateLimiter (asyncio)."""

    @pytest.mark.asyncio
    async def test_basic_acquire_release(self):
        """Should acquire and release without errors."""
        import asyncio
        limiter = AsyncRateLimiter(concurrency=2)
        async with limiter:
            pass  # Just acquire and release


# === Thread Safety Tests ===

class TestThreadSafety:
    """Tests for thread safety."""

    def test_cache_thread_safe_set_get(self):
        """Cache should be thread-safe for concurrent set/get."""
        cache = Cache(default_ttl=60)
        errors = []

        def worker(i):
            try:
                cache.set(f'key{i}', f'value{i}')
                _ = cache.get(f'key{i}')
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0

    def test_rate_limiter_thread_safe(self):
        """Rate limiter should be thread-safe."""
        limiter = RateLimiter(max_calls=100, period=60)
        errors = []

        def worker():
            try:
                for _ in range(10):
                    limiter.record_call()
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        stats = limiter.get_stats()
        assert stats['total_calls'] == 50
