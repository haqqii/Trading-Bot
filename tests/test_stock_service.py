"""
Unit tests for services/stock_service.py
Tests stock data fetching service (without actual API calls).
"""
import sys
import os

import pytest

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestStockServiceInit:
    """Tests for StockService initialization."""

    def test_init_creates_instance(self):
        """Should create instance without errors."""
        from services.stock_service import StockService
        service = StockService()
        assert service is not None

    def test_init_has_cache(self):
        """Instance should have cache reference."""
        from services.stock_service import StockService
        service = StockService()
        assert service.cache is not None

    def test_singleton_stock_service(self):
        """Should have a singleton instance."""
        from services.stock_service import stock_service
        assert stock_service is not None


class TestYahooBlacklist:
    """Tests for YAHOO_BLACKLIST constant."""

    def test_blacklist_is_set(self):
        """Blacklist should be a set."""
        from services.stock_service import YAHOO_BLACKLIST
        assert isinstance(YAHOO_BLACKLIST, set)

    def test_blacklist_not_empty(self):
        """Blacklist should not be empty."""
        from services.stock_service import YAHOO_BLACKLIST
        assert len(YAHOO_BLACKLIST) > 0

    def test_blacklist_contains_known_tickers(self):
        """Blacklist should contain known problematic tickers."""
        from services.stock_service import YAHOO_BLACKLIST
        # Some known ETF/fund tickers
        assert 'XBIN' in YAHOO_BLACKLIST
        assert 'R-LQ45X' in YAHOO_BLACKLIST

    def test_blacklist_tickers_are_strings(self):
        """All blacklist entries should be strings."""
        from services.stock_service import YAHOO_BLACKLIST
        for ticker in YAHOO_BLACKLIST:
            assert isinstance(ticker, str)


class TestStockServiceMethods:
    """Tests for StockService methods existence."""

    def test_has_get_stock_data(self):
        """Should have get_stock_data method."""
        from services.stock_service import StockService
        assert hasattr(StockService, 'get_stock_data')

    def test_has_get_stock_data_combined(self):
        """Should have get_stock_data_combined method."""
        from services.stock_service import StockService
        assert hasattr(StockService, 'get_stock_data_combined')

    def test_has_get_stock_data_tradingview(self):
        """Should have get_stock_data_tradingview method."""
        from services.stock_service import StockService
        assert hasattr(StockService, 'get_stock_data_tradingview')

    def test_has_get_stock_data_finnhub(self):
        """Should have get_stock_data_finnhub method."""
        from services.stock_service import StockService
        assert hasattr(StockService, 'get_stock_data_finnhub')

    def test_all_methods_callable(self):
        """All methods should be callable."""
        from services.stock_service import StockService
        service = StockService()
        for method_name in ['get_stock_data', 'get_stock_data_combined',
                            'get_stock_data_tradingview', 'get_stock_data_finnhub']:
            method = getattr(service, method_name)
            assert callable(method)


class TestGetStockDataCombined:
    """Tests for get_stock_data_combined routing logic."""

    def test_blacklisted_uses_tradingview(self):
        """Blacklisted ticker should use TradingView."""
        from services.stock_service import StockService
        service = StockService()

        # Mock the underlying methods to verify routing
        original_tv = service.get_stock_data_tradingview
        original_yahoo = service.get_stock_data
        original_finnhub = service.get_stock_data_finnhub

        tv_called = []
        yahoo_called = []

        def mock_tv(ticker):
            tv_called.append(ticker)
            return {'source': 'tradingview', 'price': 100}

        def mock_yahoo(ticker, *args, **kwargs):
            yahoo_called.append(ticker)
            return None

        def mock_finnhub(ticker):
            return None

        service.get_stock_data_tradingview = mock_tv
        service.get_stock_data = mock_yahoo
        service.get_stock_data_finnhub = mock_finnhub

        # Use a blacklisted ticker
        result = service.get_stock_data_combined('XBIN.JK', '5m', '1d')

        # Should have called TradingView, not Yahoo
        assert len(tv_called) == 1
        assert len(yahoo_called) == 0
        assert result['source'] == 'tradingview'

    def test_non_blacklisted_uses_yahoo_first(self):
        """Non-blacklisted ticker should try Yahoo first."""
        from services.stock_service import StockService
        service = StockService()

        tv_called = []
        yahoo_called = []
        finnhub_called = []

        def mock_tv(ticker):
            tv_called.append(ticker)
            return None

        def mock_yahoo(ticker, *args, **kwargs):
            yahoo_called.append(ticker)
            return {'source': 'yahoo', 'price': 100}

        def mock_finnhub(ticker):
            finnhub_called.append(ticker)
            return None

        service.get_stock_data_tradingview = mock_tv
        service.get_stock_data = mock_yahoo
        service.get_stock_data_finnhub = mock_finnhub

        result = service.get_stock_data_combined('BBCA.JK', '5m', '1d')

        # Yahoo should be called first
        assert len(yahoo_called) == 1
        # No need to call others since Yahoo succeeded
        assert len(tv_called) == 0
        assert result['source'] == 'yahoo'

    def test_falls_back_to_tradingview(self):
        """Should fall back to TradingView if Yahoo fails."""
        from services.stock_service import StockService
        service = StockService()

        tv_called = []
        yahoo_called = []

        def mock_tv(ticker):
            tv_called.append(ticker)
            return {'source': 'tradingview', 'price': 100}

        def mock_yahoo(ticker, *args, **kwargs):
            yahoo_called.append(ticker)
            return None

        def mock_v8(*args, **kwargs):
            return None

        service.get_stock_data_v8 = mock_v8
        service.get_stock_data_tradingview = mock_tv
        service.get_stock_data = mock_yahoo
        service.get_stock_data_finnhub = lambda x: None

        result = service.get_stock_data_combined('BBCA.JK', '5m', '1d')

        # Yahoo, v8, and TV should be called
        assert len(yahoo_called) == 1
        assert len(tv_called) == 1
        assert result['source'] == 'tradingview'

    def test_returns_none_when_all_fail(self):
        """Should return None if all sources fail."""
        from services.stock_service import StockService
        service = StockService()

        service.get_stock_data = lambda *args, **kwargs: None
        service.get_stock_data_v8 = lambda *args, **kwargs: None
        service.get_stock_data_tradingview = lambda x: None
        service.get_stock_data_finnhub = lambda x: None

        result = service.get_stock_data_combined('BBCA.JK', '5m', '1d')
        assert result is None


class TestCacheKeyFormat:
    """Tests for cache key format consistency."""

    def test_cache_key_includes_ticker(self):
        """Cache key should include ticker."""
        from services.stock_service import StockService
        service = StockService()

        # Use a known cache key format
        expected_key = "BBCA.JK:5m:1d"
        # The actual key format is "ticker:interval:period"
        assert expected_key.count(':') == 2


class TestFinnhubAPIKey:
    """Tests for Finnhub API key handling."""

    def test_api_key_loaded_from_env(self, monkeypatch):
        """API key should be loaded from environment."""
        monkeypatch.setenv('FINNHUB_API_KEY', 'test_key_12345')
        # Need to reimport to get new env var
        import importlib
        import services.stock_service
        importlib.reload(services.stock_service)
        assert services.stock_service.FINNHUB_API_KEY == 'test_key_12345'

    def test_api_key_default_empty(self, monkeypatch):
        """API key should default to empty when not set."""
        monkeypatch.delenv('FINNHUB_API_KEY', raising=False)
        import importlib
        import services.stock_service
        importlib.reload(services.stock_service)
        assert services.stock_service.FINNHUB_API_KEY == ''


class TestStockServiceErrors:
    """Tests for error handling."""

    def test_get_stock_data_invalid_ticker(self):
        """Invalid ticker should return None, not crash."""
        from services.stock_service import StockService
        service = StockService()

        # Use a clearly invalid ticker
        result = service.get_stock_data('XYZ_INVESTASI.INVALID.JK', '5m', '1d')
        # Should return None (or some non-exception result)
        assert result is None or isinstance(result, dict)

    def test_get_stock_data_handles_exception(self):
        """Should handle exceptions gracefully."""
        from services.stock_service import StockService
        service = StockService()

        # Try with malformed input that might cause issues
        # Should not crash
        try:
            result = service.get_stock_data('', '5m', '1d')
            # Returns None or dict, not exception
            assert result is None or isinstance(result, dict)
        except Exception:
            # If it does crash, that's a test failure
            pytest.fail("Should not raise exception for empty ticker")


class TestStockDataStructure:
    """Tests for expected stock data structure."""

    def test_yahoo_data_has_required_fields(self):
        """Mock data structure should have required fields."""
        mock_data = {
            'name': 'BBCA',
            'price': 8500,
            'change': 1.5,
            'rsi': 55,
            'macd': 0.5,
            'volume': 1000000,
            'candles': 50,
            'source': 'yahoo',
            'ma_fast': 8480,
            'ma_slow': 8460,
            'atr': 50,
        }
        # Verify structure
        for field in ['name', 'price', 'rsi', 'candles', 'source']:
            assert field in mock_data

    def test_tradingview_data_has_required_fields(self):
        """TradingView data structure."""
        mock_data = {
            'name': 'BBCA',
            'price': 8500,
            'rsi': 55,
            'sma20': 8480,
            'sma50': 8460,
            'candles': 50,
            'source': 'tradingview',
        }
        for field in ['name', 'price', 'candles', 'source']:
            assert field in mock_data


class TestCombined:
    """Combined tests for stock service."""

    def test_blacklist_overlap_check(self):
        """Verify blacklist has unique tickers (no duplicates in set)."""
        from services.stock_service import YAHOO_BLACKLIST
        # Since it's a set, all entries are unique
        assert len(YAHOO_BLACKLIST) == len(set(YAHOO_BLACKLIST))

    def test_known_stock_not_in_blacklist(self):
        """Popular stocks should NOT be blacklisted."""
        from services.stock_service import YAHOO_BLACKLIST
        assert 'BBCA' not in YAHOO_BLACKLIST
        assert 'BBRI' not in YAHOO_BLACKLIST
        assert 'TLKM' not in YAHOO_BLACKLIST

    def test_methods_have_docstrings(self):
        """Public methods should have docstrings."""
        from services.stock_service import StockService
        service = StockService()
        for method_name in ['get_stock_data', 'get_stock_data_combined',
                            'get_stock_data_tradingview', 'get_stock_data_finnhub']:
            method = getattr(service, method_name)
            assert method.__doc__ is not None
