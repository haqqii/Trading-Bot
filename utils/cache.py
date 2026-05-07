"""
Thread-safe in-memory cache with TTL (Time-To-Live) support.
Includes stale-while-revalidate pattern for resilient caching.
"""
import time
import threading
import logging
from typing import Any, Optional, Dict, Tuple, Callable

logger = logging.getLogger(__name__)


class Cache:
    """
    Thread-safe in-memory cache with TTL (Time-To-Live) support.
    Includes stale-while-revalidate pattern for resilient caching.
    """
    def __init__(self, default_ttl: int = 60, stale_ttl: int = 300):
        self._cache: Dict[str, Tuple[Any, float]] = {}  # key -> (value, expiry_time)
        self._stale_cache: Dict[str, Tuple[Any, float]] = {}  # key -> (value, expiry_time)
        self._lock = threading.Lock()
        self._default_ttl = default_ttl
        self._stale_ttl = stale_ttl  # How long to keep stale data
        self._stats = {'hits': 0, 'misses': 0, 'stale_hits': 0, 'sets': 0, 'stale_sets': 0}
        self._errors: Dict[str, str] = {}  # key -> last error
        self._refresh_in_progress: Dict[str, bool] = {}  # keys being refreshed

    def get(self, key: str) -> Optional[Any]:
        """Get value from cache, returns None if expired or not found"""
        with self._lock:
            if key in self._cache:
                value, expiry = self._cache[key]
                now = time.time()
                if now < expiry:
                    self._stats['hits'] += 1
                    return value
                else:
                    # Move to stale cache for stale-while-revalidate
                    if value is not None:
                        self._stale_cache[key] = (value, now + self._stale_ttl)
                    del self._cache[key]

        self._stats['misses'] += 1
        return None

    def get_stale(self, key: str) -> Optional[Any]:
        """Get stale value from cache (for fallback when API is down)"""
        with self._lock:
            if key in self._stale_cache:
                value, expiry = self._stale_cache[key]
                if time.time() < expiry:
                    self._stats['stale_hits'] += 1
                    return value
                else:
                    del self._stale_cache[key]
        return None

    def get_or_fetch(self, key: str, fetch_func: Callable, ttl: Optional[int] = None,
                     stale_ttl: Optional[int] = None) -> Tuple[Optional[Any], bool]:
        """
        Get from cache or fetch with fallback to stale data.
        Returns (value, is_stale) tuple.
        """
        # Try fresh cache first
        value = self.get(key)
        if value is not None:
            return value, False

        # Check if refresh is already in progress
        with self._lock:
            if self._refresh_in_progress.get(key):
                # Use stale data while refresh is happening
                stale_value = self.get_stale(key)
                if stale_value is not None:
                    return stale_value, True
                return None, False

        # Try stale cache as fallback
        stale_value = self.get_stale(key)
        if stale_value is not None:
            # Trigger background refresh
            return stale_value, True

        return None, False

    def set(self, key: str, value: Any, ttl: Optional[int] = None):
        """Set value in cache with TTL (seconds)"""
        if ttl is None:
            ttl = self._default_ttl
        with self._lock:
            self._cache[key] = (value, time.time() + ttl)
            self._stats['sets'] += 1
            # Clear any previous error for this key
            self._errors.pop(key, None)
            # Clear refresh flag
            self._refresh_in_progress.pop(key, None)

    def set_stale(self, key: str, value: Any, ttl: Optional[int] = None):
        """Set value in stale cache (for error recovery)"""
        if ttl is None:
            ttl = self._stale_ttl
        with self._lock:
            self._stale_cache[key] = (value, time.time() + ttl)
            self._stats['stale_sets'] += 1

    def set_error(self, key: str, error_msg: str):
        """Record error for a cache key"""
        with self._lock:
            self._errors[key] = error_msg

    def get_last_error(self, key: str) -> Optional[str]:
        """Get last error for a cache key"""
        with self._lock:
            return self._errors.get(key)

    def mark_refresh_start(self, key: str):
        """Mark that a refresh is in progress for this key"""
        with self._lock:
            self._refresh_in_progress[key] = True

    def mark_refresh_end(self, key: str):
        """Mark that a refresh has completed for this key"""
        with self._lock:
            self._refresh_in_progress.pop(key, None)

    def delete(self, key: str):
        """Delete key from cache"""
        with self._lock:
            self._cache.pop(key, None)
            self._stale_cache.pop(key, None)
            self._errors.pop(key, None)
            self._refresh_in_progress.pop(key, None)

    def clear(self):
        """Clear all cache"""
        with self._lock:
            self._cache.clear()
            self._stale_cache.clear()
            self._errors.clear()
            self._refresh_in_progress.clear()

    def cleanup(self):
        """Remove expired entries"""
        now = time.time()
        with self._lock:
            # Clean fresh cache
            expired = [k for k, (_, exp) in self._cache.items() if now >= exp]
            for k in expired:
                val = self._cache.pop(k)[0]
                # Move to stale cache
                if val is not None:
                    self._stale_cache[k] = (val, now + self._stale_ttl)

            # Clean stale cache
            stale_expired = [k for k, (_, exp) in self._stale_cache.items() if now >= exp]
            for k in stale_expired:
                del self._stale_cache[k]

    def stats(self) -> Dict[str, int]:
        """Return cache statistics"""
        with self._lock:
            return self._stats.copy()

    def hit_rate(self) -> float:
        """Return cache hit rate (0-1)"""
        s = self.stats()
        total = s['hits'] + s['misses']
        return s['hits'] / total if total > 0 else 0

    def effective_hit_rate(self) -> float:
        """Return effective hit rate including stale hits (0-1)"""
        s = self.stats()
        total = s['hits'] + s['stale_hits'] + s['misses']
        return (s['hits'] + s['stale_hits']) / total if total > 0 else 0


# Global cache instances
_price_cache = Cache(default_ttl=60, stale_ttl=300)      # 1 min fresh, 5 min stale for price
_signal_cache = Cache(default_ttl=120, stale_ttl=600)       # 2 min fresh, 10 min stale for signals
_market_cache = Cache(default_ttl=300, stale_ttl=1800)     # 5 min fresh, 30 min stale for market
_usd_cache = Cache(default_ttl=300, stale_ttl=3600)        # 5 min fresh, 1 hour stale for USD rate
