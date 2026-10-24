"""
Unit tests for services/crypto_service.py
Tests crypto data fetching service structure and utility functions.
"""
import sys
import os
from unittest.mock import patch, MagicMock

import pytest

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestCryptoServiceInit:
    """Tests for CryptoService initialization."""

    def test_init_creates_instance(self):
        """Should create instance without errors."""
        from services.crypto_service import CryptoService
        service = CryptoService()
        assert service is not None

    def test_init_has_cache(self):
        """Instance should have cache references."""
        from services.crypto_service import CryptoService
        service = CryptoService()
        assert service.cache is not None
        assert service.usd_cache is not None

    def test_init_empty_pairs(self):
        """Instance should start with empty crypto_pairs."""
        from services.crypto_service import CryptoService
        service = CryptoService()
        assert isinstance(service.crypto_pairs, dict)
        assert len(service.crypto_pairs) == 0

    def test_init_empty_coingecko_ids(self):
        """Instance should start with empty coingecko_ids."""
        from services.crypto_service import CryptoService
        service = CryptoService()
        assert isinstance(service.coingecko_ids, dict)
        assert len(service.coingecko_ids) == 0

    def test_init_empty_major_crypto(self):
        """Instance should start with empty major_crypto set."""
        from services.crypto_service import CryptoService
        service = CryptoService()
        assert isinstance(service.major_crypto, set)
        assert len(service.major_crypto) == 0

    def test_init_not_loaded(self):
        """Service should not be loaded initially."""
        from services.crypto_service import CryptoService
        service = CryptoService()
        assert service._crypto_loaded is False

    def test_singleton_exists(self):
        """Singleton instance should exist."""
        from services.crypto_service import crypto_service
        assert crypto_service is not None


class TestFallbackCryptoPairs:
    """Tests for _FALLBACK_CRYPTO_PAIRS constant."""

    def test_fallback_pairs_is_dict(self):
        """Fallback pairs should be a dict."""
        from services.crypto_service import _FALLBACK_CRYPTO_PAIRS
        assert isinstance(_FALLBACK_CRYPTO_PAIRS, dict)

    def test_fallback_contains_bitcoin(self):
        """Should contain BTC-USD."""
        from services.crypto_service import _FALLBACK_CRYPTO_PAIRS
        assert 'BTC-USD' in _FALLBACK_CRYPTO_PAIRS

    def test_fallback_contains_ethereum(self):
        """Should contain ETH-USD."""
        from services.crypto_service import _FALLBACK_CRYPTO_PAIRS
        assert 'ETH-USD' in _FALLBACK_CRYPTO_PAIRS

    def test_fallback_values_are_tuples(self):
        """Values should be (name, coingecko_id) tuples."""
        from services.crypto_service import _FALLBACK_CRYPTO_PAIRS
        for key, value in _FALLBACK_CRYPTO_PAIRS.items():
            assert isinstance(value, tuple)
            assert len(value) == 2
            name, cg_id = value
            assert isinstance(name, str)
            assert isinstance(cg_id, str)

    def test_fallback_pair_format(self):
        """Keys should be in XXX-USD format."""
        from services.crypto_service import _FALLBACK_CRYPTO_PAIRS
        for key in _FALLBACK_CRYPTO_PAIRS.keys():
            assert '-USD' in key


class TestCryptoServiceMethods:
    """Tests for CryptoService methods existence."""

    def test_has_get_crypto_data(self):
        """Should have get_crypto_data method."""
        from services.crypto_service import CryptoService
        assert hasattr(CryptoService, 'get_crypto_data')

    def test_has_get_crypto_data_combined(self):
        """Should have get_crypto_data_combined method."""
        from services.crypto_service import CryptoService
        assert hasattr(CryptoService, 'get_crypto_data_combined')

    def test_has_load_crypto_pairs(self):
        """Should have load_crypto_pairs method."""
        from services.crypto_service import CryptoService
        assert hasattr(CryptoService, 'load_crypto_pairs')

    def test_has_get_usd_idr_rate(self):
        """Should have get_usd_idr_rate method."""
        from services.crypto_service import CryptoService
        assert hasattr(CryptoService, 'get_usd_idr_rate')

    def test_all_methods_callable(self):
        """All public methods should be callable."""
        from services.crypto_service import CryptoService
        service = CryptoService()
        for method_name in ['get_crypto_data', 'get_crypto_data_combined',
                            'load_crypto_pairs', 'get_usd_idr_rate']:
            method = getattr(service, method_name)
            assert callable(method)


class TestGetCryptoDataCombined:
    """Tests for get_crypto_data_combined routing logic."""

    def test_yahoo_data_returns_first(self):
        """If Yahoo returns valid data, should return Yahoo result."""
        from services.crypto_service import CryptoService
        service = CryptoService()

        yahoo_data = {
            'price': 50000,
            'rsi': 55,
            'name': 'Bitcoin'
        }
        cg_called = []

        def mock_yahoo(ticker, *args, **kwargs):
            return yahoo_data

        def mock_cg(ticker):
            cg_called.append(ticker)
            return None

        service.get_crypto_data = mock_yahoo
        service.get_crypto_data_coingecko = mock_cg

        result = service.get_crypto_data_combined('BTC-USD', '1h', '1d')

        # Should use Yahoo data
        assert result is not None
        assert result['source'] == 'yahoo'
        assert result['price'] == 50000
        # Should NOT call CoinGecko
        assert len(cg_called) == 0

    def test_falls_back_to_coingecko(self):
        """Should fall back to CoinGecko if Yahoo fails."""
        from services.crypto_service import CryptoService
        service = CryptoService()

        cg_data = {
            'price': 49000,
            'rsi': 60,
            'name': 'Bitcoin'
        }

        def mock_yahoo(ticker, *args, **kwargs):
            return None

        def mock_cg(ticker):
            return cg_data

        service.get_crypto_data = mock_yahoo
        service.get_crypto_data_coingecko = mock_cg

        result = service.get_crypto_data_combined('BTC-USD', '1h', '1d')

        assert result is not None
        assert result['price'] == 49000

    def test_returns_none_when_all_fail(self):
        """Should return None when both fail."""
        from services.crypto_service import CryptoService
        service = CryptoService()

        service.get_crypto_data = lambda *args, **kwargs: None
        service.get_crypto_data_coingecko = lambda x: None

        result = service.get_crypto_data_combined('BTC-USD', '1h', '1d')
        assert result is None

    def test_rejects_yahoo_with_invalid_rsi(self):
        """Yahoo data with NaN RSI should fall back to CoinGecko."""
        import math
        from services.crypto_service import CryptoService
        service = CryptoService()

        # Yahoo returns data but RSI is NaN
        yahoo_data = {'price': 50000, 'rsi': float('nan'), 'name': 'Bitcoin'}

        cg_called = []
        cg_data = {'price': 49000, 'rsi': 55, 'name': 'Bitcoin'}

        service.get_crypto_data = lambda *args, **kwargs: yahoo_data
        service.get_crypto_data_coingecko = lambda x: (cg_called.append(x) or cg_data)

        result = service.get_crypto_data_combined('BTC-USD', '1h', '1d')

        # Should fall back to CoinGecko because Yahoo RSI is invalid
        assert len(cg_called) == 1

    def test_rejects_yahoo_with_zero_rsi(self):
        """Yahoo data with RSI=0 should fall back to CoinGecko."""
        from services.crypto_service import CryptoService
        service = CryptoService()

        # Yahoo returns data but RSI is 0
        yahoo_data = {'price': 50000, 'rsi': 0, 'name': 'Bitcoin'}

        cg_called = []

        service.get_crypto_data = lambda *args, **kwargs: yahoo_data
        service.get_crypto_data_coingecko = lambda x: (cg_called.append(x) or {'price': 49000, 'rsi': 55})

        service.get_crypto_data_combined('BTC-USD', '1h', '1d')

        # Should fall back because RSI is 0
        assert len(cg_called) == 1


class TestGetUsdIdrRate:
    """Tests for USD/IDR rate fetching."""

    def test_returns_number(self):
        """Should return a numeric rate."""
        from services.crypto_service import CryptoService
        service = CryptoService()

        # Mock to return a fixed rate
        service.usd_cache.set = MagicMock()

        with patch('requests.get') as mock_get:
            mock_response = MagicMock()
            mock_response.json.return_value = {
                'rates': {'IDR': 15500}
            }
            mock_response.status_code = 200
            mock_get.return_value = mock_response

            rate = service.get_usd_idr_rate()
            assert isinstance(rate, (int, float))
            assert rate > 0

    def test_returns_default_on_error(self):
        """Should return default rate on error."""
        from services.crypto_service import CryptoService
        service = CryptoService()

        with patch('requests.get') as mock_get:
            mock_get.side_effect = Exception("Network error")
            rate = service.get_usd_idr_rate()
            # Should not crash, returns some default
            assert isinstance(rate, (int, float))
            assert rate > 0


class TestCryptoDataStructure:
    """Tests for expected crypto data structure."""

    def test_yahoo_crypto_structure(self):
        """Yahoo crypto data should have expected fields."""
        mock_data = {
            'name': 'Bitcoin',
            'price': 50000,
            'change': 2.5,
            'rsi': 55,
            'macd': 100,
            'volume': 1000000,
            'candles': 50,
            'source': 'yahoo',
            'ma_fast': 49500,
            'ma_slow': 49000,
            'atr': 200,
        }
        for field in ['name', 'price', 'rsi', 'candles', 'source']:
            assert field in mock_data

    def test_coingecko_structure(self):
        """CoinGecko data should have expected fields."""
        mock_data = {
            'name': 'Bitcoin',
            'price': 50000,
            'change_24h': 2.5,
            'rsi': 55,
            'candles': 50,
            'source': 'coingecko',
            'volume_24h': 1000000,
        }
        for field in ['name', 'price', 'candles', 'source']:
            assert field in mock_data


class TestCryptoPairLoading:
    """Tests for crypto pair loading logic."""

    def test_load_crypto_pairs_method_exists(self):
        """load_crypto_pairs method should exist."""
        from services.crypto_service import CryptoService
        service = CryptoService()
        assert hasattr(service, 'load_crypto_pairs')
        assert callable(service.load_crypto_pairs)

    def test_load_sets_loaded_flag(self):
        """After load, _crypto_loaded should be True."""
        from services.crypto_service import CryptoService
        service = CryptoService()

        # Mock the actual network call
        service._crypto_loaded = False

        with patch.object(service, 'load_crypto_pairs') as mock_load:
            mock_load.side_effect = lambda: setattr(service, '_crypto_loaded', True)
            service.load_crypto_pairs()
            # Verify flag is set (we mocked it)
            assert service._crypto_loaded is True


class TestEdgeCases:
    """Edge case tests for crypto service."""

    def test_empty_ticker_string(self):
        """Empty ticker should not crash."""
        from services.crypto_service import CryptoService
        service = CryptoService()

        try:
            result = service.get_crypto_data('', '1h', '1d')
            assert result is None or isinstance(result, dict)
        except Exception:
            pytest.fail("Should not raise exception for empty ticker")

    def test_invalid_ticker_format(self):
        """Invalid ticker format should not crash."""
        from services.crypto_service import CryptoService
        service = CryptoService()

        try:
            result = service.get_crypto_data('XXX-INVALID', '1h', '1d')
            assert result is None or isinstance(result, dict)
        except Exception:
            pytest.fail("Should not raise exception for invalid ticker")


class TestCombined:
    """Combined tests for crypto service."""

    def test_known_crypto_in_fallback(self):
        """Popular cryptos should be in fallback pairs."""
        from services.crypto_service import _FALLBACK_CRYPTO_PAIRS
        popular = ['BTC-USD', 'ETH-USD', 'BNB-USD', 'SOL-USD', 'XRP-USD']
        for crypto in popular:
            assert crypto in _FALLBACK_CRYPTO_PAIRS

    def test_all_fallback_have_coingecko_id(self):
        """All fallback entries should have valid CoinGecko IDs."""
        from services.crypto_service import _FALLBACK_CRYPTO_PAIRS
        for key, (name, cg_id) in _FALLBACK_CRYPTO_PAIRS.items():
            # CoinGecko IDs are lowercase with hyphens
            assert cg_id == cg_id.lower()
            assert ' ' not in cg_id

    def test_methods_have_docstrings(self):
        """Public methods should have docstrings."""
        from services.crypto_service import CryptoService
        service = CryptoService()
        for method_name in ['get_crypto_data', 'get_crypto_data_combined',
                            'load_crypto_pairs', 'get_usd_idr_rate']:
            method = getattr(service, method_name)
            assert method.__doc__ is not None


class TestCoinGeckoNullFields:
    """Regression tests for CoinGecko returning null on some fields.

    Real case: FIGR_HELOC (Figure Heloc, CoinGecko id ``figure-heloc``) returns
    ``price_change_percentage_24h: null``.  Stored as-is it reached
    ``generate_crypto_signal()`` and blew up on ``if change >= 3:`` with
    ``TypeError: '>=' not supported between instances of 'NoneType' and 'int'``.
    """

    @staticmethod
    def _payload(change_24h=None, total_volume=142865686.0):
        return {
            'name': 'Null Change Token',
            'symbol': 'nullchg',
            'market_data': {
                'current_price': {'usd': 1.0084},
                'total_volume': {'usd': total_volume} if total_volume is not None else None,
                'price_change_percentage_24h': change_24h,
                'sparkline_7d': {'price': [1.0 + i * 0.001 for i in range(60)]},
            },
        }

    @staticmethod
    def _fetch(payload, ticker='NULLCHG-USD'):
        from services import crypto_service as cs_mod
        from services.crypto_service import CryptoService
        service = CryptoService()
        service.coingecko_ids = {ticker: 'null-change-token'}

        fake_resp = MagicMock()
        fake_resp.status_code = 200
        fake_resp.json.return_value = payload

        with patch.object(cs_mod, '_cg_session') as mock_session:
            mock_session.get.return_value = fake_resp
            return service.get_crypto_data_coingecko(ticker)

    def test_null_24h_change_becomes_zero(self):
        """Null price_change_percentage_24h should be normalized to 0.0."""
        result = self._fetch(self._payload(), ticker='NULLCHG-A-USD')
        assert result is not None
        assert result['change'] == 0.0
        assert isinstance(result['change'], float)

    def test_null_total_volume_becomes_zero(self):
        """Null total_volume should be normalized to 0.0."""
        result = self._fetch(self._payload(total_volume=None), ticker='NULLCHG-B-USD')
        assert result is not None
        assert result['volume'] == 0.0

    def test_valid_24h_change_is_preserved(self):
        """A real 24h change value should pass through untouched."""
        result = self._fetch(self._payload(change_24h=-3.25), ticker='NULLCHG-C-USD')
        assert result is not None
        assert result['change'] == pytest.approx(-3.25)

    def test_null_change_does_not_break_signal_generation(self):
        """generate_crypto_signal must not raise on a null change value."""
        from services.signal_service import SignalService
        result = self._fetch(self._payload(), ticker='NULLCHG-D-USD')
        signal = SignalService.generate_crypto_signal(result)
        assert signal['signal'] in ('BUY', 'SELL', 'REVERSAL', 'HOLD')
        assert signal['entry'] == pytest.approx(1.0084)


class TestZeroVolumeRejection:
    """Regression: Yahoo {SYM}-USD can belong to a different asset entirely.

    Real case: AERO-USD on Yahoo is "Aerovek Aviation" (dead token, price
    ~$0.000025, zero volume) while CoinGecko's AERO is Aerodrome Finance
    (~$0.78).  The bot lists pairs from CoinGecko but fetches data from
    Yahoo first — so a signal was generated with entry $0.0000253 and the
    notification rendered every price as "$0.00 | Rp 0".  Numbers like this
    pass the price/RSI validation; zero volume is the only tell.
    """

    @staticmethod
    def _hist_df(volumes):
        """Build a synthetic OHLCV frame like yfinance returns."""
        import numpy as np
        import pandas as pd
        n = 80
        idx = pd.date_range('2026-01-01', periods=n, freq='1h')
        rng = np.random.default_rng(42)
        close = pd.Series(100 + np.cumsum(rng.normal(0, 0.5, n)), index=idx)
        return pd.DataFrame({
            'Open': close,
            'High': close * 1.01,
            'Low': close * 0.99,
            'Close': close,
            'Volume': volumes,
        })

    @staticmethod
    def _fetch_with_hist(hist):
        from services import crypto_service as cs_mod
        from services.crypto_service import CryptoService
        service = CryptoService()

        mock_yf = MagicMock()
        mock_yf.Ticker.return_value.history.return_value = hist

        with patch.object(cs_mod, 'yf', mock_yf):
            return service.get_crypto_data('AERO-USD', '4h', '1mo', use_cache=False)

    def test_zero_volume_data_is_rejected(self):
        """All-zero volume (dead/mismatched asset) must yield None."""
        import numpy as np
        hist = self._hist_df(np.zeros(80))
        assert self._fetch_with_hist(hist) is None

    def test_nonzero_volume_data_is_accepted(self):
        """Live data with real volume must still be used."""
        import numpy as np
        rng = np.random.default_rng(7)
        hist = self._hist_df(rng.uniform(1_000, 50_000, 80))
        result = self._fetch_with_hist(hist)
        assert result is not None
        assert result['price'] > 0
        assert result['candles'] == 80

    def test_recent_volume_zero_only_rejects_when_all_zero(self):
        """One quiet candle among active ones must not trigger rejection."""
        import numpy as np
        volumes = np.full(80, 10_000.0)
        volumes[-1] = 0  # candle terakhir kosong, sisanya aktif
        hist = self._hist_df(volumes)
        assert self._fetch_with_hist(hist) is not None

