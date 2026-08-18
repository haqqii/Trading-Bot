"""Custom exceptions and error handling utilities for the bot."""


class APIError(Exception):
    """Base exception for API-related errors."""
    def __init__(self, message: str, source: str = "unknown", retry_after: int = 0):
        super().__init__(message)
        self.source = source
        self.retry_after = retry_after  # seconds to wait before retry


class RateLimitError(APIError):
    """Raised when API rate limit is hit."""
    def __init__(self, source: str, retry_after: int = 60):
        super().__init__(
            f"Rate limit exceeded for {source}. Wait {retry_after}s.",
            source=source,
            retry_after=retry_after
        )


class ServiceUnavailableError(APIError):
    """Raised when a service is temporarily unavailable."""
    def __init__(self, source: str, message: str = ""):
        msg = message or f"{source} is temporarily unavailable"
        super().__init__(msg, source=source, retry_after=30)


class DataNotFoundError(APIError):
    """Raised when requested data is not found."""
    def __init__(self, ticker: str, source: str = "unknown"):
        super().__init__(
            f"Data not found for ticker: {ticker}",
            source=source
        )
        self.ticker = ticker


class InsufficientDataError(APIError):
    """Raised when there's not enough data to process."""
    def __init__(self, ticker: str, available: int, required: int, source: str = "unknown"):
        super().__init__(
            f"Insufficient data for {ticker}: {available} < {required}",
            source=source
        )
        self.ticker = ticker
        self.available = available
        self.required = required


def format_user_error(error: Exception) -> str:
    """Format exception into user-friendly message."""
    if isinstance(error, RateLimitError):
        return (
            f"⚠️ Rate limit untuk {error.source}.\n"
            f"Coba lagi dalam {error.retry_after} detik."
        )
    elif isinstance(error, ServiceUnavailableError):
        return (
            f"⚠️ {error.source} sedang unavailable.\n"
            f"Silakan coba beberapa saat lagi."
        )
    elif isinstance(error, DataNotFoundError):
        return (
            f"❌ Data tidak ditemukan: {error.ticker}\n"
            f"Pastikan kode yang dimasukkan benar."
        )
    elif isinstance(error, InsufficientDataError):
        return (
            f"❌ Data tidak cukup untuk {error.ticker}.\n"
            f"Tersedia {error.available} candle, butuh minimal {error.required}."
        )
    elif isinstance(error, APIError):
        return f"⚠️ Error dari {error.source}: {str(error)[:100]}"
    else:
        # Generic error - don't expose internal details to users
        return "⚠️ Terjadi kesalahan teknis. Silakan coba lagi."
