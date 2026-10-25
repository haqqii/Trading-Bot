"""Fixture bersama untuk seluruh test suite.

Limiter dan circuit breaker di utils.rate_limiter adalah global tingkat
modul. Tanpa di-reset, test yang mensimulasikan kegagalan provider bisa
membuka circuit breaker (threshold 10 kegagalan), lalu test sesudahnya
di file lain ikut gagal karena "CoinGecko circuit breaker OPEN".
"""
import pytest

from utils import rate_limiter


@pytest.fixture(autouse=True)
def reset_api_guards():
    """Kosongkan jendela limiter global dan tutup semua breaker per test."""
    for limiter in (rate_limiter._coingecko_limiter, rate_limiter._yahoo_limiter):
        with limiter.lock:
            limiter.calls.clear()
    rate_limiter.reset_all_circuit_breakers()
    yield
