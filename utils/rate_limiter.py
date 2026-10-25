"""
Rate limiting and circuit breaker patterns for API protection.
"""
import time
import random
import asyncio
import json
import logging
import os
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

    def __init__(self, max_calls=10, period=60, name="default",
                 state_file: Optional[str] = None, reserved: int = 0):
        self.max_calls = max_calls
        self.period = period
        self.calls = deque()
        self.lock = threading.Lock()
        # Condition di atas lock yang sama: menunggu slot melepas lock,
        # jadi pemanggil lain (terutama yang prioritas) tidak ikut terblokir
        # selama satu pemanggil sedang tidur.
        self._cond = threading.Condition(self.lock)
        self.name = name
        # Sisa kuota yang khusus untuk pemanggil prioritas (command yang
        # dipicu user). Pemanggil background dibatasi max_calls - reserved,
        # jadi request user tidak perlu antre belasan menit di belakang
        # scan yang sedang jalan. Total request per menit tetap max_calls.
        self.reserved = max(0, int(reserved))
        self._total_calls = 0
        self._total_waits = 0
        self._total_rejected = 0
        # Kalau diisi, riwayat panggilan disimpan ke file supaya tetap
        # berlaku setelah container di-restart. Batas request provider itu
        # per-IP, jadi limiter baru yang lupa request lama bisa menembak
        # kuota orang lain di IP yang sama.
        self.state_file = state_file
        if state_file:
            self._restore_state()

    def _restore_state(self):
        """Baca riwayat panggilan dari file (dari proses sebelumnya)."""
        try:
            with open(self.state_file, encoding='utf-8') as f:
                stamps = json.load(f)
            now = time.time()
            with self.lock:
                for stamp in stamps:
                    stamp = float(stamp)
                    # Abaikan yang sudah lewat masa jendela, dan yang
                    # timestamp-nya jauh di masa depan (jam meleset) -
                    # kalau tidak, limiter bisa terkunci lama.
                    if now - stamp < self.period and stamp - now < 5:
                        self.calls.append(stamp)
        except Exception:
            # File belum ada / rusak - limiter tetap jalan normal.
            pass

    def _persist_state(self):
        """Simpan riwayat panggilan ke file."""
        try:
            folder = os.path.dirname(self.state_file)
            if folder:
                os.makedirs(folder, exist_ok=True)
            now = time.time()
            recent = [s for s in self.calls if now - s < self.period]
            tmp = f"{self.state_file}.tmp"
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(recent, f)
            os.replace(tmp, self.state_file)
        except Exception:
            # Gagal menyimpan bukan alasan untuk menghentikan bot.
            pass

    def _limit_for(self, priority: bool) -> int:
        """Batas request yang boleh dipakai pemanggil ini.

        Pemanggil prioritas boleh memakai seluruh kuota; pemanggil
        background hanya boleh memakai kuota yang tidak dipesan untuk
        pemanggil prioritas.
        """
        if priority:
            return self.max_calls
        return max(1, self.max_calls - self.reserved)

    def can_call(self, priority: bool = False):
        """Check if we can make a call without waiting"""
        with self.lock:
            now = time.time()
            while self.calls and self.calls[0] < now - self.period:
                self.calls.popleft()
            return len(self.calls) < self._limit_for(priority)

    def wait_if_needed(self, priority: bool = False):
        """Tunggu sampai ada slot, lalu catat panggilan.

        Menunggu lewat Condition, bukan time.sleep di bawah lock. Kalau
        tidurnya di bawah lock, satu pemanggil yang sedang nunggu memblokir
        semua pemanggil lain - termasuk yang prioritas, yang justru paling
        butuh cepat. Condition melepas lock selama menunggu.
        """
        waited = False
        with self._cond:
            while True:
                now = time.time()
                while self.calls and self.calls[0] < now - self.period:
                    self.calls.popleft()

                if len(self.calls) < self._limit_for(priority):
                    self.calls.append(time.time())
                    self._total_calls += 1
                    if self.state_file:
                        self._persist_state()
                    self._cond.notify_all()
                    return

                if not waited:
                    self._total_waits += 1
                    waited = True
                wait_time = self.calls[0] + self.period - now
                # Potongan pendek supaya jendela waktu dan pemanggil
                # prioritas selalu dicek ulang.
                self._cond.wait(timeout=max(0.1, min(wait_time, 1.0)))

    def record_call(self):
        """Record a successful call"""
        with self.lock:
            self.calls.append(time.time())
            self._total_calls += 1
            if self.state_file:
                self._persist_state()

    def get_stats(self) -> Dict[str, Any]:
        """Get rate limiter stats"""
        with self.lock:
            return {
                'name': self.name,
                'max_calls': self.max_calls,
                'period': self.period,
                'reserved': self.reserved,
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
        return str(self.state.value).upper()

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
# CoinGecko free tier: batasnya per-IP dan kadang lebih ketat dari
# dokumentasi, jadi 4 request/menit (bukan 5). Limiter CoinGecko juga
# menyimpan riwayat panggilannya ke file, supaya container yang di-restart
# tidak lupa request yang dibuat proses sebelumnya - kalau lupa, burst
# saat start bisa kena 429 dan daftar pair jatuh ke daftar cadangan.
_COINGECKO_STATE_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data',
    'coingecko_calls.json')
_coingecko_limiter = RateLimiter(max_calls=4, period=60, name="coingecko",
                                 state_file=_COINGECKO_STATE_FILE, reserved=2)
# 2 dari 4 request/menit ke CoinGecko dikhususkan untuk jalur yang butuh
# cepat (job cek TP/SL, command user). Scan dan prefetch dibatasi 2/menit,
# dan biasanya cuma pakai 1 request per 15 menit - jadi sisa kuotanya
# hampir tidak pernah dipakai, tapi kalau ada burst (misal setelah
# restart) job TP/SL tidak lagi menunggu hampir semenit.
_yahoo_limiter = RateLimiter(max_calls=20, period=60, name="yahoo",
                             reserved=4)  # Reduced from 30 to avoid rate limit
# Sisa 4 request/menit dikhususkan untuk jalur yang butuh cepat: command
# yang dipicu user (/analisa, /harga) dan job cek TP/SL. Tanpa itu, mereka
# antre di belakang scan crypto yang sedang pakai kuota - dulu /analisa
# baru selesai 2 menit 44 detik setelah command-nya masuk.
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


def coingecko_can_call_now(priority: bool = False) -> bool:
    """True kalau limiter CoinGecko punya slot kosong sekarang juga.

    Dipakai pemanggil yang tidak boleh menunggu lama (job cek TP/SL):
    menunggu slot bisa memakan hampir semenit, dan itu sendiri sudah
    cukup untuk bikin satu siklus lewat batas 2 menit.

    priority=True: jalur yang butuh cepat boleh memakai kuota cadangan,
    jadi syaratnya lebih longgar.
    """
    return _coingecko_limiter.can_call(priority=priority)


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

