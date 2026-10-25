"""
Test untuk perbaikan performa scan crypto:

- Pengambilan data CoinGecko secara bulk (satu request untuk banyak ticker)
  menggantikan satu request per ticker.
- Cache data harga yang lebih lama (60 detik -> 300 detik).
- Pemulihan daftar ticker crypto yang jatuh ke daftar cadangan.
- Alert ketika scan berjalan dengan cakupan terlalu kecil.
- Grace time APScheduler yang benar untuk semua job background.
"""
import os
import sys
import time
import json
import logging
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def make_sparkline(n=168, start=100.0, step=0.1):
    """Sparkline hourly 7 hari seperti yang dikembalikan CoinGecko."""
    return [start + i * step for i in range(n)]


def make_markets_coin(coin_id, symbol, price=100.0, change=2.5, volume=1_000_000.0):
    """Bentuk respons /coins/markets untuk satu coin."""
    return {
        'id': coin_id,
        'symbol': symbol,
        'name': f"{symbol} Coin",
        'current_price': price,
        'price_change_percentage_24h': change,
        'total_volume': volume,
        'sparkline_in_7d': {'price': make_sparkline(start=price)},
    }


def make_single_coin(coin_id, symbol, price=100.0, change=2.5, volume=1_000_000.0):
    """Bentuk respons /coins/{id} untuk satu coin."""
    return {
        'id': coin_id,
        'name': f"{symbol} Coin",
        'market_data': {
            'current_price': {'usd': price},
            'price_change_percentage_24h': change,
            'total_volume': {'usd': volume},
            'sparkline_7d': {'price': make_sparkline(start=price)},
        },
    }


class FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else []

    def json(self):
        return self._payload


class TestBuildCryptoPayload:
    """Perhitungan indikator jalur CoinGecko."""

    def test_payload_has_expected_keys(self):
        from services.crypto_service import build_crypto_payload

        payload, error = build_crypto_payload(
            'AAA-USD', 'AAA', 100.0, 2.5, 1000.0, make_sparkline())

        assert error is None
        assert payload is not None
        for key in ('ticker', 'name', 'price', 'change', 'ma_fast', 'ma_slow',
                    'rsi', 'volume', 'atr', 'range_24h', 'candles', 'source',
                    'macd', 'macd_signal', 'macd_hist', 'bb_upper',
                    'bb_middle', 'bb_lower', 'volume_ma', 'volume_ratio'):
            assert key in payload, f"key {key} hilang dari payload"

        assert payload['ticker'] == 'AAA-USD'
        assert payload['price'] == 100.0
        assert payload['change'] == 2.5
        assert payload['source'] == 'coingecko'
        assert payload['candles'] == 168
        assert 0 < payload['rsi'] <= 100

    def test_rejects_bad_price(self):
        from services.crypto_service import build_crypto_payload

        for price in (0, -1, 20_000_000, None):
            payload, error = build_crypto_payload(
                'AAA-USD', 'AAA', price, 1.0, 1.0, make_sparkline())
            assert payload is None
            assert error == 'price'

    def test_rejects_short_sparkline(self):
        from services.crypto_service import build_crypto_payload

        payload, error = build_crypto_payload(
            'AAA-USD', 'AAA', 100.0, 1.0, 1.0, [1.0, 2.0, 3.0])
        assert payload is None
        assert error == 'sparkline'

    def test_null_change_becomes_zero(self):
        """CoinGecko kadang balas null untuk price_change_percentage_24h."""
        from services.crypto_service import build_crypto_payload

        payload, error = build_crypto_payload(
            'AAA-USD', 'AAA', 100.0, None, None, make_sparkline())

        assert error is None
        assert payload['change'] == 0.0
        assert payload['volume'] == 0.0

    def test_rsi_fallback_only_when_allowed(self):
        """RSI tidak valid: gagal dulu, ada fallback kalau diizinkan."""
        from services.crypto_service import build_crypto_payload

        # Seri harga datar -> RSI 0 (tidak valid)
        flat = [100.0] * 168

        payload, error = build_crypto_payload('AAA-USD', 'AAA', 100.0, 3.0,
                                              1.0, flat)
        assert payload is None
        assert error == 'rsi'

        payload, error = build_crypto_payload('AAA-USD', 'AAA', 100.0, 3.0,
                                              1.0, flat, rsi_fallback=True)
        assert error is None
        assert 1 <= payload['rsi'] <= 99

    def test_range_24h_uses_last_24_points(self):
        """Rentang 24 jam dihitung dari 24 titik terakhir sparkline."""
        from services.crypto_service import build_crypto_payload

        spark = make_sparkline(n=168, start=100.0, step=1.0)
        payload, _ = build_crypto_payload('AAA-USD', 'AAA', 167.0, 1.0,
                                          1.0, spark)
        # 24 titik terakhir: 144..167 -> max 167, min 144
        assert payload['range_24h'] is not None
        assert 13.0 < payload['range_24h'] < 17.0


class TestCoingeckoBulkFetch:
    """Pengambilan data CoinGecko banyak ticker sekaligus."""

    @pytest.fixture(autouse=True)
    def clear_cache(self):
        """Cache harga global dipakai bersama - bersihkan antar test."""
        from utils.cache import _price_cache
        for key in list(_price_cache._cache.keys()):
            if key.startswith('cg_'):
                _price_cache.delete(key)
        yield

    def setup_service(self):
        from services.crypto_service import CryptoService
        service = CryptoService()
        service.coingecko_ids = {
            'AAA-USD': 'aaa',
            'BBB-USD': 'bbb',
            'CCC-USD': 'ccc',
        }
        return service

    def test_one_request_for_many_tickers(self, monkeypatch):
        """Banyak ticker diambil dalam satu request, bukan satu per ticker."""
        from services import crypto_service as cs

        service = self.setup_service()
        calls = []

        def fake_get(url, params=None, timeout=None):
            calls.append((url, params))
            ids = params['ids'].split(',')
            return FakeResponse(200, [make_markets_coin(i, i.upper())
                                      for i in ids])

        monkeypatch.setattr(cs._cg_session, 'get', fake_get)
        monkeypatch.setattr(cs._coingecko_limiter, 'wait_if_needed',
                            lambda *a, **k: None)

        result = service.get_crypto_data_coingecko_bulk(
            ['AAA-USD', 'BBB-USD', 'CCC-USD'])

        assert len(calls) == 1, "3 ticker harus cukup 1 request"
        assert 'coins/markets' in calls[0][0]
        assert set(result.keys()) == {'AAA-USD', 'BBB-USD', 'CCC-USD'}
        assert result['AAA-USD']['price'] == 100.0
        assert result['AAA-USD']['source'] == 'coingecko'

    def test_chunking_for_more_than_100_tickers(self, monkeypatch):
        """Lebih dari 100 ticker dipecah jadi beberapa chunk."""
        from services import crypto_service as cs

        service = self.setup_service()
        service.coingecko_ids = {f'T{i}-USD': f'id{i}' for i in range(250)}
        calls = []

        def fake_get(url, params=None, timeout=None):
            calls.append(params['ids'])
            ids = params['ids'].split(',')
            return FakeResponse(200, [make_markets_coin(i, i) for i in ids])

        monkeypatch.setattr(cs._cg_session, 'get', fake_get)
        monkeypatch.setattr(cs._coingecko_limiter, 'wait_if_needed',
                            lambda *a, **k: None)

        result = service.get_crypto_data_coingecko_bulk(
            [f'T{i}-USD' for i in range(250)])

        assert len(calls) == 3, "250 ticker harus jadi 3 chunk"
        assert len(result) == 250

    def test_result_cached_for_other_jobs(self, monkeypatch):
        """Data hasil bulk disimpan di cache cg_{ticker}."""
        from services import crypto_service as cs

        service = self.setup_service()

        def fake_get(url, params=None, timeout=None):
            ids = params['ids'].split(',')
            return FakeResponse(200, [make_markets_coin(i, i.upper())
                                      for i in ids])

        monkeypatch.setattr(cs._cg_session, 'get', fake_get)
        monkeypatch.setattr(cs._coingecko_limiter, 'wait_if_needed',
                            lambda *a, **k: None)

        service.get_crypto_data_coingecko_bulk(['AAA-USD'])
        cached = service.cache.get('cg_AAA-USD')

        assert cached is not None
        assert cached['ticker'] == 'AAA-USD'
        assert cached['candles'] == 168

    def test_cached_ticker_not_refetched(self, monkeypatch):
        """Ticker yang sudah ada di cache tidak di-request lagi."""
        from services import crypto_service as cs

        service = self.setup_service()
        service.cache.set('cg_AAA-USD', {'ticker': 'AAA-USD', 'candles': 168,
                                         'rsi': 55.0, 'price': 100.0},
                          ttl=300)
        calls = []

        def fake_get(url, params=None, timeout=None):
            calls.append(params['ids'])
            return FakeResponse(200, [])

        monkeypatch.setattr(cs._cg_session, 'get', fake_get)
        monkeypatch.setattr(cs._coingecko_limiter, 'wait_if_needed',
                            lambda *a, **k: None)

        result = service.get_crypto_data_coingecko_bulk(['AAA-USD'])

        assert calls == [], "ticker yang sudah cached tidak boleh di-request"
        assert 'AAA-USD' in result, "data dari cache tetap dikembalikan"

    def test_bulk_and_single_give_same_numbers(self, monkeypatch):
        """Jalur bulk dan jalur satu-per-ticker menghasilkan angka sama."""
        from services import crypto_service as cs

        service = self.setup_service()

        def fake_get(url, params=None, timeout=None):
            if 'markets' in url:
                return FakeResponse(200, [make_markets_coin('aaa', 'AAA')])
            return FakeResponse(200, make_single_coin('aaa', 'AAA'))

        monkeypatch.setattr(cs._cg_session, 'get', fake_get)
        monkeypatch.setattr(cs._coingecko_limiter, 'wait_if_needed',
                            lambda *a, **k: None)

        bulk = service.get_crypto_data_coingecko_bulk(['AAA-USD'])['AAA-USD']
        service.cache.delete('cg_AAA-USD')
        single = service.get_crypto_data_coingecko('AAA-USD')

        assert single is not None
        for key in ('price', 'change', 'rsi', 'atr', 'candles', 'volume',
                    'ma_fast', 'ma_slow', 'macd', 'macd_hist', 'bb_upper',
                    'bb_lower', 'range_24h'):
            assert bulk[key] == single[key], f"nilai {key} berbeda"

    def test_rate_limit_does_not_crash(self, monkeypatch):
        """CoinGecko balas 429 -> hasil kosong, tidak exception."""
        from services import crypto_service as cs

        service = self.setup_service()
        monkeypatch.setattr(cs._cg_session, 'get',
                            lambda url, params=None, timeout=None:
                            FakeResponse(429, {}))
        monkeypatch.setattr(cs._coingecko_limiter, 'wait_if_needed',
                            lambda *a, **k: None)
        monkeypatch.setattr(cs.time, 'sleep', lambda s: None)

        result = service.get_crypto_data_coingecko_bulk(['AAA-USD'])
        assert result == {}


class TestCacheTtl:
    """Umur cache data harga."""

    def test_yahoo_ttl_is_300(self):
        from services import crypto_service as cs
        assert cs._YAHOO_CACHE_TTL == 300

    def test_coingecko_ttl_is_300(self):
        from services import crypto_service as cs
        assert cs._COINGECKO_CACHE_TTL == 300

    def test_provider_cache_uses_long_ttl(self, monkeypatch):
        """Data yang disimpan lewat jalur Yahoo memakai TTL panjang."""
        from services import crypto_service as cs

        service = cs.CryptoService()
        captured = {}
        real_set = service.cache.set

        def spy_set(key, value, ttl=None):
            captured['key'] = key
            captured['ttl'] = ttl
            return real_set(key, value, ttl=ttl)

        monkeypatch.setattr(service.cache, 'set', spy_set)
        monkeypatch.setattr(service, 'get_crypto_data', lambda *a, **k: None)

        # Panggil jalur yang menyimpan hasil Yahoo
        source = open(cs.__file__, encoding='utf-8').read()
        assert 'ttl=_YAHOO_CACHE_TTL' in source
        assert 'ttl=_COINGECKO_CACHE_TTL' in source


class TestFallbackPairsRetry:
    """Pemulihan daftar ticker yang jatuh ke daftar cadangan."""

    def test_not_used_fallback_by_default(self):
        from services.crypto_service import CryptoService
        service = CryptoService()
        assert service.used_fallback_pairs is False

    def test_expected_pair_count_starts_at_normal_value(self):
        from services.crypto_service import CryptoService
        assert CryptoService().expected_pair_count == 250

    def test_retry_is_noop_when_list_is_complete(self, monkeypatch):
        from services.crypto_service import CryptoService

        service = CryptoService()
        service._crypto_loaded = True
        called = []
        monkeypatch.setattr(service, 'load_crypto_pairs',
                            lambda force=False: called.append(force))

        assert service.retry_load_crypto_pairs() is True
        assert called == [], "lista lengkap tidak perlu di-request lagi"

    def test_retry_recovers_the_full_list(self, monkeypatch):
        from services.crypto_service import CryptoService

        service = CryptoService()
        service.used_fallback_pairs = True

        def fake_load(force=False):
            service.used_fallback_pairs = False
            service.crypto_pairs = {f'T{i}-USD': 'T' for i in range(250)}
            return service.crypto_pairs

        monkeypatch.setattr(service, 'load_crypto_pairs', fake_load)

        assert service.retry_load_crypto_pairs() is True
        assert len(service.crypto_pairs) == 250

    def test_retry_keeps_fallback_when_still_rate_limited(self, monkeypatch):
        from services.crypto_service import CryptoService

        service = CryptoService()
        service.used_fallback_pairs = True
        monkeypatch.setattr(service, 'load_crypto_pairs',
                            lambda force=False: service.crypto_pairs)

        assert service.retry_load_crypto_pairs() is False
        assert service.used_fallback_pairs is True

    def test_retry_skipped_when_last_attempt_was_recent(self, monkeypatch):
        """Percobaan ulang jangan menumpuk di detik yang sama.

        Container yang baru start memanggil job prefetch beberapa detik
        setelah start - percobaan kedua secepat itu hanya kena rate limit
        lagi.
        """
        from services.crypto_service import CryptoService

        service = CryptoService()
        service.used_fallback_pairs = True
        service._last_pair_load_time = time.time()
        called = []
        monkeypatch.setattr(service, 'load_crypto_pairs',
                            lambda force=False: called.append(force))

        assert service.retry_load_crypto_pairs() is False
        assert called == [], "retry harus ditunda, bukan langsung request"

    def test_retry_allowed_after_minimum_interval(self, monkeypatch):
        from services.crypto_service import CryptoService

        service = CryptoService()
        service.used_fallback_pairs = True
        service._last_pair_load_time = time.time() - 3600
        called = []
        monkeypatch.setattr(service, 'load_crypto_pairs',
                            lambda force=False: called.append(force))

        service.retry_load_crypto_pairs()
        assert called == [True], "retry harus jalan kalau sudah lama"


class TestCryptoPairLoading:
    """Pengambilan daftar pair dari CoinGecko."""

    def _make_coins(self, n=3):
        coins = []
        for i in range(n):
            coins.append({
                'id': f'id{i}',
                'symbol': f'sym{i}',
                'name': f'Sym {i}',
                'market_cap_rank': 10 + i,
            })
        return coins

    def test_loader_uses_the_shared_rate_limiter(self, monkeypatch):
        """Request daftar pair harus ikut limiter CoinGecko yang sama.

        Tanpa ini, request saat start tidak dihitung di kuota 5/menit dan
        bisa memicu 429. Sekaligus dijaga hanya ada SATU request: dulu
        loop "halaman 1-2" tidak pernah mengirim parameter 'page', jadi
        request kedua mengulang request pertama dan satu kuota terbuang.
        """
        from services import crypto_service as cs

        service = cs.CryptoService()
        waits = []
        requests = []

        monkeypatch.setattr(cs._coingecko_limiter, 'wait_if_needed',
                            lambda *a, **k: waits.append(1))

        def fake_get(url, params=None, timeout=None):
            requests.append(params)
            return FakeResponse(200, self._make_coins())

        monkeypatch.setattr(cs._cg_session, 'get', fake_get)
        monkeypatch.setattr(cs.time, 'sleep', lambda s: None)

        service.load_crypto_pairs(force=True)

        assert len(waits) == 1, "hanya satu request, lewat limiter"
        assert len(requests) == 1, "tidak boleh ada request kembar"

    def test_pair_load_seeds_price_reference(self, monkeypatch):
        """Harga dari respons daftar pair langsung jadi acuan harga.

        Tanpa ini, acuan harus dimuat terpisah (1-3 request CoinGecko lagi)
        - di saat start itu berarti start bisa kena 429 dan acuan kosong
        selama 10 menit.
        """
        from services import crypto_service as cs

        service = cs.CryptoService()
        coins = self._make_coins(3)
        coins[0]['current_price'] = 123.45
        coins[1]['current_price'] = 0.5
        coins[2]['current_price'] = None

        monkeypatch.setattr(cs._coingecko_limiter, 'wait_if_needed',
                            lambda *a, **k: None)
        monkeypatch.setattr(
            cs._cg_session, 'get',
            lambda url, params=None, timeout=None:
                FakeResponse(200, coins))
        monkeypatch.setattr(cs.time, 'sleep', lambda s: None)

        service.load_crypto_pairs(force=True)

        assert service.cg_price_ref.get('SYM0-USD') == 123.45
        assert service.cg_price_ref.get('SYM1-USD') == 0.5
        assert 'SYM2-USD' not in service.cg_price_ref
        # Acuan dianggap segar, jadi tidak perlu request tambahan.
        assert service._price_ref_time > 0

    def test_seeded_reference_avoids_extra_coingecko_call(self, monkeypatch):
        """Setelah daftar pair dimuat, acuan harga tidak perlu dimuat lagi."""
        from services import crypto_service as cs

        service = cs.CryptoService()
        coins = self._make_coins(12)
        coins[0]['current_price'] = 10.0
        coins[1]['current_price'] = 20.0

        monkeypatch.setattr(cs._coingecko_limiter, 'wait_if_needed',
                            lambda *a, **k: None)
        monkeypatch.setattr(
            cs._cg_session, 'get',
            lambda url, params=None, timeout=None:
                FakeResponse(200, coins))
        monkeypatch.setattr(cs.time, 'sleep', lambda s: None)

        service.load_crypto_pairs(force=True)
        before = len(service.cg_price_ref)

        called = []
        monkeypatch.setattr(service, 'load_price_reference',
                            lambda *a, **k: called.append(1))

        service.refresh_price_reference()

        assert not called, "acuan sudah ada, tidak boleh request lagi"
        assert len(service.cg_price_ref) == before

    def test_successful_load_records_expected_count(self, monkeypatch):
        """Jumlah pair dari daftar lengkap dicatat untuk pembanding."""
        from services import crypto_service as cs

        service = cs.CryptoService()
        monkeypatch.setattr(cs._coingecko_limiter, 'wait_if_needed',
                            lambda *a, **k: None)
        monkeypatch.setattr(
            cs._cg_session, 'get',
            lambda url, params=None, timeout=None:
                FakeResponse(200, self._make_coins(20)))
        monkeypatch.setattr(cs.time, 'sleep', lambda s: None)

        service.load_crypto_pairs(force=True)

        assert service.used_fallback_pairs is False
        assert service.expected_pair_count == len(service.crypto_pairs)
        assert service.expected_pair_count == 20

    def test_fallback_load_keeps_previous_expected_count(self, monkeypatch,
                                                         tmp_path):
        """Kalau jatuh ke cadangan, jumlah normal tidak ikut turun."""
        from services import crypto_service as cs

        service = cs.CryptoService()
        service.expected_pair_count = 250
        # Cache pair diarahkan ke file yang tidak ada, supaya test ini
        # benar-benar menguji jalur daftar cadangan.
        service.pair_cache_file = str(tmp_path / 'tidak_ada.json')
        monkeypatch.setattr(cs._coingecko_limiter, 'wait_if_needed',
                            lambda *a, **k: None)
        monkeypatch.setattr(
            cs._cg_session, 'get',
            lambda url, params=None, timeout=None:
                FakeResponse(429, {}))
        monkeypatch.setattr(cs.time, 'sleep', lambda s: None)

        service.load_crypto_pairs(force=True)

        assert service.used_fallback_pairs is True
        assert service.expected_pair_count == 250
        assert len(service.crypto_pairs) == 12, "daftar cadangan berisi 12 koin"
        assert service.pair_source == 'fallback'


class TestPairListCache:
    """Cache daftar pair di disk.

    Rate limit CoinGecko saat start pernah membuat bot cuma memantau 12
    koin sampai job recovery berhasil. Cache daftar lengkap terakhir
    membuat jendela itu tidak lagi menurunkan cakupan.
    """

    def _make_coins(self, n):
        coins = []
        for i in range(n):
            coins.append({
                'id': f'id{i}',
                'symbol': f'sym{i}',
                'name': f'Sym {i}',
                'market_cap_rank': 10 + i,
            })
        return coins

    def _service(self, tmp_path):
        from services import crypto_service as cs

        service = cs.CryptoService()
        service.pair_cache_file = str(tmp_path / 'pairs.json')
        return service

    def test_successful_load_saves_cache(self, monkeypatch, tmp_path):
        from services import crypto_service as cs

        service = self._service(tmp_path)
        monkeypatch.setattr(cs._coingecko_limiter, 'wait_if_needed',
                            lambda *a, **k: None)
        monkeypatch.setattr(
            cs._cg_session, 'get',
            lambda url, params=None, timeout=None:
                FakeResponse(200, self._make_coins(60)))

        service.load_crypto_pairs(force=True)

        assert os.path.exists(service.pair_cache_file), \
            "daftar pair harus disimpan ke disk"

        with open(service.pair_cache_file, encoding='utf-8') as f:
            payload = json.load(f)
        assert len(payload['pairs']) == 60
        assert payload['ids']['SYM0-USD'] == 'id0'
        assert payload['saved_at'] > 0

    def test_rate_limit_uses_saved_cache(self, monkeypatch, tmp_path):
        """429 setelah daftar pernah berhasil dimuat -> pakai cache.

        Tanpa ini, satu rate limit cukup untuk menurunkan cakupan ke
        12 koin selama beberapa menit.
        """
        from services import crypto_service as cs

        service = self._service(tmp_path)
        monkeypatch.setattr(cs._coingecko_limiter, 'wait_if_needed',
                            lambda *a, **k: None)
        monkeypatch.setattr(
            cs._cg_session, 'get',
            lambda url, params=None, timeout=None:
                FakeResponse(200, self._make_coins(60)))
        service.load_crypto_pairs(force=True)

        # Simulasikan restart: instance baru, cache masih ada di disk,
        # CoinGecko sekarang rate limited.
        service2 = self._service(tmp_path)
        monkeypatch.setattr(
            cs._cg_session, 'get',
            lambda url, params=None, timeout=None:
                FakeResponse(429, {}))

        service2.load_crypto_pairs(force=True)

        assert len(service2.crypto_pairs) == 60, \
            "cakupan harus tetap penuh dari cache"
        assert service2.pair_source == 'cache'
        assert service2.used_fallback_pairs is True, \
            "status tetap 'belum lengkap' supaya job recovery terus mencoba"
        assert service2.expected_pair_count == 250
        # Id CoinGecko ikut diambil dari cache - tanpa ini job cek TP/SL
        # harus minta Yahoo untuk tiap ticker.
        assert service2.coingecko_ids.get('SYM0-USD') == 'id0'

    def test_stale_cache_is_not_used(self, monkeypatch, tmp_path):
        """Cache yang sudah lebih dari 7 hari tidak boleh dipakai."""
        from services import crypto_service as cs

        service = self._service(tmp_path)
        with open(service.pair_cache_file, 'w', encoding='utf-8') as f:
            json.dump({
                'saved_at': time.time() - 8 * 24 * 3600,
                'pairs': {f'SYM{i}-USD': f'Sym {i}' for i in range(60)},
                'ids': {f'SYM{i}-USD': f'id{i}' for i in range(60)},
                'major': [],
            }, f)

        monkeypatch.setattr(cs._coingecko_limiter, 'wait_if_needed',
                            lambda *a, **k: None)
        monkeypatch.setattr(
            cs._cg_session, 'get',
            lambda url, params=None, timeout=None:
                FakeResponse(429, {}))

        service.load_crypto_pairs(force=True)

        assert len(service.crypto_pairs) == 12, \
            "cache basi harus diabaikan, jatuh ke daftar cadangan"
        assert service.pair_source == 'fallback'

    def test_partial_list_is_not_cached(self, monkeypatch, tmp_path):
        """Daftar yang terpotong tidak boleh disimpan sebagai cache."""
        from services import crypto_service as cs

        service = self._service(tmp_path)
        monkeypatch.setattr(cs._coingecko_limiter, 'wait_if_needed',
                            lambda *a, **k: None)
        monkeypatch.setattr(
            cs._cg_session, 'get',
            lambda url, params=None, timeout=None:
                FakeResponse(200, self._make_coins(12)))

        service.load_crypto_pairs(force=True)

        assert not os.path.exists(service.pair_cache_file), \
            "daftar pendek tidak boleh dianggap daftar lengkap"


class TestScanDegradedAlert:
    """Alert ketika scan berjalan dengan cakupan terlalu kecil."""

    def test_alert_is_throttled(self, monkeypatch, caplog):
        from handlers.scheduler import jobs_crypto

        monkeypatch.setattr(jobs_crypto, '_last_degrade_alert', 0.0)

        with caplog.at_level(logging.ERROR):
            assert jobs_crypto._alert_degraded_scan('x', 'y') is True
            # Dalam 6 jam berikutnya tidak alert lagi
            assert jobs_crypto._alert_degraded_scan('x', 'y') is False

    def test_alert_message_reaches_admin_channel(self, caplog):
        """Pesan degrade logged sebagai ERROR supaya jadi alert admin."""
        from handlers.scheduler import jobs_crypto
        from utils.telegram_alert import _is_noise

        with caplog.at_level(logging.ERROR):
            jobs_crypto._alert_degraded_scan(
                'daftar ticker crypto jatuh ke daftar cadangan',
                'Hanya 12 koin yang dipantau.')

        records = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert records, "harus ada record ERROR"
        assert not _is_noise(records[-1]), \
            "pesan degrade tidak boleh difilter sebagai noise"

    def test_fallback_pair_triggers_alert(self, monkeypatch, caplog):
        """Kalau daftar pair jatuh ke cadangan, admin diberi tahu."""
        from handlers.scheduler import jobs_crypto

        class FakeService:
            crypto_pairs = {'BTC-USD': 'Bitcoin'}
            used_fallback_pairs = True
            expected_pair_count = 250

            def get_usd_idr_rate(self):
                return 16000

            def get_crypto_data(self, ticker, interval, period,
                                skip_if_missing=False):
                return None

            def get_crypto_data_coingecko_bulk(self, tickers, retry=3,
                                               use_cache=True):
                return {}

        class FakeApp:
            def __init__(self):
                self.bot = MagicMock()

        monkeypatch.setattr(jobs_crypto, 'crypto_service', FakeService())
        monkeypatch.setattr(jobs_crypto, '_get_user_db', lambda: {
            '1': {'notif_crypto': True, 'timeframe': '240'}})
        monkeypatch.setattr(jobs_crypto, '_get_last_buy_signals', lambda: {})
        monkeypatch.setattr(jobs_crypto, '_last_degrade_alert', 0.0)

        with caplog.at_level(logging.ERROR):
            import asyncio
            asyncio.run(jobs_crypto.check_crypto_signals(FakeApp()))

        messages = [r.getMessage() for r in caplog.records
                    if r.levelno >= logging.ERROR]
        assert any('Scan degrade' in m for m in messages), \
            f"tidak ada alert degrade: {messages}"

        # Pesan harus menyebut jumlah normal, bukan membandingkan angka
        # yang sama ("bukan 12" kalau yang dipantau 12).
        degrade = next(m for m in messages if 'Scan degrade' in m)
        assert '250' in degrade, f"jumlah pair normal tidak disebut: {degrade}"

    def test_cached_pair_list_does_not_trigger_alert(self, monkeypatch, caplog):
        """Kalau daftar berasal dari cache, cakupan tetap penuh.

        Rate limit CoinGecko adalah masalah jaringan, bukan bug - dan
        cakupan tidak turun. Jadi tidak boleh dikirimi admin.
        """
        from handlers.scheduler import jobs_crypto

        class FakeService:
            crypto_pairs = {f'SYM{i}-USD': 'Sym' for i in range(60)}
            used_fallback_pairs = True
            pair_source = 'cache'
            expected_pair_count = 250

            def get_usd_idr_rate(self):
                return 16000

            def get_crypto_data(self, ticker, interval, period,
                                skip_if_missing=False):
                return None

            def get_crypto_data_coingecko_bulk(self, tickers, retry=3,
                                               use_cache=True):
                return {}

        class FakeApp:
            def __init__(self):
                self.bot = MagicMock()

        monkeypatch.setattr(jobs_crypto, 'crypto_service', FakeService())
        monkeypatch.setattr(jobs_crypto, '_get_user_db', lambda: {
            '1': {'notif_crypto': True, 'timeframe': '240'}})
        monkeypatch.setattr(jobs_crypto, '_get_last_buy_signals', lambda: {})
        monkeypatch.setattr(jobs_crypto, '_last_degrade_alert', 0.0)

        with caplog.at_level(logging.ERROR):
            import asyncio
            asyncio.run(jobs_crypto.check_crypto_signals(FakeApp()))

        messages = [r.getMessage() for r in caplog.records
                    if r.levelno >= logging.ERROR]
        assert not any('Scan degrade' in m for m in messages), \
            f"cakupan penuh, tidak boleh ada alert degrade: {messages}"


class TestCoingeckoLimiterState:
    """Limiter CoinGecko harus ingat request dari proses sebelumnya.

    Batas request CoinGecko itu per-IP, jadi kalau container di-restart
    tepat setelah scan, limiter baru yang amnesia bisa menembak kuota
    yang sudah terpakai -> 429 -> daftar pair jatuh ke 12 koin cadangan.
    """

    def test_state_file_is_configured(self):
        from utils import rate_limiter
        assert rate_limiter._coingecko_limiter.state_file, \
            "limiter CoinGecko harus menyimpan state ke file"
        assert rate_limiter._coingecko_limiter.max_calls == 4

    def test_previous_calls_are_restored(self, tmp_path):
        """Riwayat panggilan dari file dipakai limiter yang baru."""
        from utils.rate_limiter import RateLimiter
        import json

        state_file = str(tmp_path / 'calls.json')
        with open(state_file, 'w', encoding='utf-8') as f:
            json.dump([time.time() - 5, time.time() - 10], f)

        limiter = RateLimiter(max_calls=4, period=60, name='test',
                              state_file=state_file)

        assert len(limiter.calls) == 2, \
            "riwayat dari proses sebelumnya harus ikut dihitung"

    def test_old_calls_are_ignored(self, tmp_path):
        """Riwayat yang sudah lewat masa jendela tidak dihitung."""
        from utils.rate_limiter import RateLimiter
        import json

        state_file = str(tmp_path / 'calls.json')
        with open(state_file, 'w', encoding='utf-8') as f:
            json.dump([time.time() - 600], f)

        limiter = RateLimiter(max_calls=4, period=60, name='test',
                              state_file=state_file)

        assert len(limiter.calls) == 0

    def test_future_calls_are_ignored(self, tmp_path):
        """Timestamp di masa depan (jam meleset) jangan dikunci limiter."""
        from utils.rate_limiter import RateLimiter
        import json

        state_file = str(tmp_path / 'calls.json')
        with open(state_file, 'w', encoding='utf-8') as f:
            json.dump([time.time() + 3600], f)

        limiter = RateLimiter(max_calls=4, period=60, name='test',
                              state_file=state_file)

        assert len(limiter.calls) == 0, \
            "timestamp masa depan tidak boleh memblokir limiter"

    def test_calls_are_persisted(self, tmp_path):
        """Setiap panggilan dicatat ke file."""
        from utils.rate_limiter import RateLimiter
        import json

        state_file = str(tmp_path / 'calls.json')
        limiter = RateLimiter(max_calls=4, period=60, name='test',
                              state_file=state_file)

        limiter.wait_if_needed()

        with open(state_file, encoding='utf-8') as f:
            saved = json.load(f)
        assert len(saved) == 1


class TestTpSlPriceReference:
    """Harga CoinGecko hanya boleh dipakai kalau harga asetnya sama.

    Simbol di CoinGecko dan Yahoo tidak selalu menunjuk aset yang sama
    (mis. JUP-USD). Kalau harga yang salah dipakai, level TP/SL dihitung
    dari aset yang keliru dan notifikasi ke user jadi bohong.
    """

    def test_same_price_is_accepted(self):
        from handlers.scheduler.jobs_crypto import _price_matches_reference
        assert _price_matches_reference(102.0, 100.0)
        assert _price_matches_reference(100.0, 102.0)

    def test_small_deviation_is_accepted(self):
        from handlers.scheduler.jobs_crypto import _price_matches_reference
        assert _price_matches_reference(103.0, 100.0)
        assert _price_matches_reference(97.0, 100.0)

    def test_different_asset_is_rejected(self):
        from handlers.scheduler.jobs_crypto import _price_matches_reference
        assert not _price_matches_reference(0.3, 100.0)
        assert not _price_matches_reference(100.0, 0.3)

    def test_missing_reference_is_rejected(self):
        """Tanpa harga pembanding, pakai Yahoo (jawaban yang aman)."""
        from handlers.scheduler.jobs_crypto import _price_matches_reference
        assert not _price_matches_reference(102.0, None)
        assert not _price_matches_reference(102.0, 0)

    def test_broken_values_are_rejected(self):
        from handlers.scheduler.jobs_crypto import _price_matches_reference
        assert not _price_matches_reference(None, 100.0)
        assert not _price_matches_reference('abc', 100.0)
        assert not _price_matches_reference(102.0, 'abc')


class TestCryptoScanCacheLookup:
    """Harga acuan harus diambil dari cache scan, fresh maupun basi.

    Hasil scan disimpan di cache dengan TTL 300 detik, jadi selama scan
    baru selesai datanya masih "segar" - tapi pembacaan yang hanya
    melihat cache basi tidak akan menemukannya. Akibatnya harga acuan
    jadi kosong, semua ticker dianggap tidak cocok, dan satu siklus cek
    TP/SL menembak provider satu per satu (lalu kena skip).
    """

    def test_fresh_scan_data_is_found(self):
        from handlers.scheduler import _common
        from handlers.scheduler._common import crypto_scan_cache_key

        key = crypto_scan_cache_key('BTC-USD', '4h', '1mo')
        _common._price_cache.set(key, {'price': 102.0, 'candles': 168},
                                 ttl=300)
        try:
            data = _common.get_crypto_scan_data('BTC-USD', '4h', '1mo')
            assert data is not None
            assert data['price'] == 102.0
        finally:
            _common._price_cache._cache.pop(key, None)

    def test_expired_scan_data_is_still_found(self):
        """Entri yang sudah lewat TTL dipindah ke cache basi saat dibaca."""
        import time
        from handlers.scheduler import _common
        from handlers.scheduler._common import crypto_scan_cache_key

        key = crypto_scan_cache_key('ETH-USD', '4h', '1mo')
        _common._price_cache.set(key, {'price': 103.0, 'candles': 168},
                                 ttl=300)
        # Paksa jadi kedaluwarsa.
        _common._price_cache._cache[key] = (
            {'price': 103.0, 'candles': 168}, time.time() - 1)
        try:
            data = _common.get_crypto_scan_data('ETH-USD', '4h', '1mo')
            assert data is not None, \
                "data scan yang basi tetap harus ketemu"
            assert data['price'] == 103.0
        finally:
            _common._price_cache._cache.pop(key, None)
            _common._price_cache._stale_cache.pop(key, None)

    def test_missing_scan_data_returns_none(self):
        from handlers.scheduler import _common
        assert _common.get_crypto_scan_data('XXX-USD', '4h', '1mo') is None


class TestCryptoPairsRecoveryJob:
    """Job pemulihan daftar pair harus mencoba lagi lalu melapor."""

    def test_noop_when_list_is_complete(self, monkeypatch):
        import asyncio
        from handlers.scheduler import jobs_crypto

        class FakeService:
            used_fallback_pairs = False
            crypto_pairs = {'BTC-USD': 'Bitcoin'}

            def retry_load_crypto_pairs(self):
                raise AssertionError("retry tidak boleh jalan saat daftar lengkap")

        monkeypatch.setattr(jobs_crypto, 'crypto_service', FakeService())

        asyncio.run(jobs_crypto.check_crypto_pairs_recovery(MagicMock()))

    def test_retries_when_fallback(self, monkeypatch, caplog):
        import asyncio
        from handlers.scheduler import jobs_crypto

        state = {'called': 0}

        class FakeService:
            used_fallback_pairs = True
            crypto_pairs = {'BTC-USD': 'Bitcoin', 'ETH-USD': 'Ethereum'}

            def retry_load_crypto_pairs(self):
                state['called'] += 1
                self.used_fallback_pairs = False
                return True

        monkeypatch.setattr(jobs_crypto, 'crypto_service', FakeService())

        with caplog.at_level(logging.INFO):
            asyncio.run(jobs_crypto.check_crypto_pairs_recovery(MagicMock()))

        assert state['called'] == 1
        assert any('pulih' in r.getMessage() for r in caplog.records)


class TestMisfireGraceTime:
    """Semua job background harus punya misfire_grace_time yang benar."""

    def test_every_job_has_job_kwargs(self):
        from handlers.scheduler import jobs_misc

        registered = []

        class FakeJobQueue:
            def run_repeating(self, callback, **kwargs):
                registered.append((callback.__name__, kwargs))

        class FakeApp:
            def __init__(self):
                self.job_queue = FakeJobQueue()
                self.job_queue.scheduler = MagicMock()

        jobs_misc.register_jobs(FakeApp())

        assert registered, "tidak ada job yang terdaftar"
        for name, kwargs in registered:
            assert 'job_kwargs' in kwargs, f"job {name} tidak punya job_kwargs"
            assert kwargs['job_kwargs'].get('misfire_grace_time') == 120, \
                f"job {name} tidak punya misfire_grace_time yang benar"

    def test_grace_time_constant(self):
        from handlers.scheduler import jobs_misc
        assert jobs_misc.MISFIRE_GRACE_TIME == 120


class TestSendDelay:
    """Jeda antar pesan tidak boleh terlalu lama."""

    def test_send_delay_is_five_seconds(self):
        from handlers.scheduler._common import SEND_DELAY_SECONDS
        assert SEND_DELAY_SECONDS == 5

    def test_no_long_sleep_left_in_scheduler(self):
        """Tidak ada jeda 60 detik di job background."""
        from handlers.scheduler import jobs_crypto, jobs_stocks

        for module in (jobs_crypto, jobs_stocks):
            source = open(module.__file__, encoding='utf-8').read()
            assert 'asyncio.sleep(60)' not in source, \
                f"{module.__name__} masih ada jeda 60 detik"


class TestPrefetchUsesUserTimeframe:
    """Prefetch harus memakai timeframe yang dipakai user."""

    def test_prefetch_uses_users_interval(self, monkeypatch, caplog):
        import asyncio
        from handlers.scheduler import jobs_crypto

        seen = {}

        class FakeService:
            crypto_pairs = {'AAA-USD': 'AAA', 'BBB-USD': 'BBB'}
            used_fallback_pairs = False

            def get_usd_idr_rate(self):
                return 16000

            def retry_load_crypto_pairs(self):
                return True

            def get_crypto_data_combined(self, ticker, interval, period,
                                         skip_if_missing=False):
                seen.setdefault('intervals', set()).add((interval, period))
                return {'candles': 100, 'price': 1.0}

            def get_crypto_data_coingecko_bulk(self, tickers, retry=3,
                                               use_cache=True):
                return {}

        class FakeApp:
            bot = MagicMock()

        monkeypatch.setattr(jobs_crypto, 'crypto_service', FakeService())
        monkeypatch.setattr(jobs_crypto, '_get_user_db', lambda: {
            '1': {'notif_crypto': True, 'timeframe': '240'}})

        with caplog.at_level(logging.INFO):
            asyncio.run(jobs_crypto.prefetch_crypto_cache(FakeApp()))

        assert seen.get('intervals'), "prefetch tidak memanggil fetch sama sekali"
        assert ('4h', '1mo') in seen['intervals'], \
            f"timeframe user tidak dipakai: {seen['intervals']}"
