"""
Crypto data fetching service using Yahoo Finance and CoinGecko.
"""
import time
import logging
import requests
import yfinance as yf
import pandas as pd
import warnings
import math
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from utils.cache import _price_cache, _usd_cache
from utils.rate_limiter import _yahoo_limiter, _coingecko_limiter, _circuit_breakers, exponential_backoff
from utils.log_filters import DropNoPriceData, install_provider_filters
from utils.indicators import (
    calculate_rsi, calculate_macd, calculate_bollinger_bands,
    calculate_volume_metrics, calculate_vwap, calculate_stochastic,
    calculate_adx, calculate_ichimoku, calculate_fibonacci_retracement,
    calculate_sr_levels
)

logger = logging.getLogger(__name__)

# HTTP session with connection pooling
_cg_session = requests.Session()
_cg_adapter = HTTPAdapter(
    pool_connections=20,
    pool_maxsize=20,
    max_retries=0,
    pool_block=False
)
_cg_session.mount('http://', _cg_adapter)
_cg_session.mount('https://', _cg_adapter)
_cg_session.headers.update({'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'})

# Fallback crypto pairs
_FALLBACK_CRYPTO_PAIRS = {
    'BTC-USD': ('Bitcoin', 'bitcoin'), 'ETH-USD': ('Ethereum', 'ethereum'),
    'BNB-USD': ('BNB', 'binancecoin'), 'SOL-USD': ('Solana', 'solana'),
    'XRP-USD': ('XRP', 'ripple'), 'ADA-USD': ('Cardano', 'cardano'),
    'DOGE-USD': ('Dogecoin', 'dogecoin'), 'DOT-USD': ('Polkadot', 'polkadot'),
    'MATIC-USD': ('Polygon', 'matic-network'), 'AVAX-USD': ('Avalanche', 'avalanche-2'),
    'LINK-USD': ('Chainlink', 'chainlink'), 'UNI-USD': ('Uniswap', 'uniswap'),
}

COINGECKO_IDS: dict[str, str] = {}

# Namespace ticker Yahoo Finance ({SYM}-USD) tidak sama dengan CoinGecko.
# Simbol yang sama belum tentu aset yang sama:
#   JUP-USD di Yahoo = aset lain ($0.00026), JUP di CoinGecko = Jupiter ($0.30)
#   AERO-USD di Yahoo = Aerovek Aviation (token mati), AERO = Aerodrome Finance
# Kalau harga Yahoo beda lebih dari rasio ini dari harga acuan CoinGecko,
# datanya dicurigai milik aset lain dan ditolak.
_PRICE_MISMATCH_RATIO = 5.0

# Umur acuan harga (detik). Harga token bergerak, tapi toleransinya lebar
# (5x), jadi acuan cukup beberapa kali refresh per siklus scan. Sengaja
# pendek supaya cakupan yang sempat hilang (chunk kena rate limit) cepat
# dilengkapi lagi.
_PRICE_REF_TTL = 600

# Jumlah id per request /simple/price (URL-nya tidak boleh terlalu panjang).
_PRICE_REF_CHUNK = 100


class CryptoService:
    """Service for fetching crypto data"""

    def __init__(self):
        self.cache = _price_cache
        self.usd_cache = _usd_cache
        self.crypto_pairs = {}
        self.coingecko_ids = {}
        self.major_crypto = set()
        self._crypto_loaded = False
        # Harga acuan dari CoinGecko untuk mencocokkan hasil Yahoo Finance.
        self.cg_price_ref: dict[str, float] = {}
        self._price_ref_time: float = 0.0

    def load_crypto_pairs(self):
        """Auto-load crypto pairs from CoinGecko (top 500 coins) with fallback"""
        if self._crypto_loaded:
            return self.crypto_pairs

        coingecko_mappings = {}
        major_set = set()
        cg_failed = False

        try:
            url = "https://api.coingecko.com/api/v3/coins/markets"
            params = {
                'vs_currency': 'usd',
                'order': 'market_cap_desc',
                'per_page': 250,
                'sparkline': 'false',
                'price_change_percentage': '24h'
            }

            # Fetch pages 1-2 (top 500 coins) - reduce from 5 to avoid rate limits
            for page in [1, 2]:
                try:
                    resp = _cg_session.get(url, params=params, timeout=10)
                except Exception as req_err:
                    logger.warning(f"CoinGecko request failed: {req_err}")
                    cg_failed = True
                    break

                if resp.status_code == 200:
                    coins = resp.json()
                    for coin in coins:
                        sym = coin.get('symbol', '').upper()
                        cg_id = coin.get('id', '')
                        name = coin.get('name', '')
                        rank = coin.get('market_cap_rank') or 999
                        if sym and cg_id:
                            ticker = f"{sym}-USD"
                            coingecko_mappings[ticker] = (name, cg_id)
                            if rank <= 100:
                                major_set.add(ticker)
                elif resp.status_code == 429:
                    # Rate limited - skip immediately, use fallback
                    logger.warning(f"CoinGecko rate limited, skipping to fallback")
                    cg_failed = True
                    break
                else:
                    logger.warning(f"CoinGecko page {page} error: {resp.status_code}")
                    cg_failed = True
                    break

                # Delay between pages
                time.sleep(1)

            logger.info(f"CoinGecko: fetched {len(coingecko_mappings)} coins")
        except Exception as e:
            logger.warning(f"CoinGecko fetch failed: {e}, falling back to hardcoded list")
            cg_failed = True

        # Fallback if CoinGecko failed or returned too few coins
        if cg_failed or len(coingecko_mappings) < 10:
            logger.warning(f"Using fallback crypto list ({len(_FALLBACK_CRYPTO_PAIRS)} coins)")
            # Merge with what we got from CoinGecko (prefer CoinGecko data)
            for ticker, value in _FALLBACK_CRYPTO_PAIRS.items():
                if ticker not in coingecko_mappings:
                    coingecko_mappings[ticker] = value

        self.crypto_pairs = {ticker: name for ticker, (name, _) in coingecko_mappings.items()}
        self.coingecko_ids = {ticker: cg_id for ticker, (_, cg_id) in coingecko_mappings.items()}
        self.major_crypto = major_set & set(self.crypto_pairs.keys())
        self._crypto_loaded = True
        logger.info(f"Crypto loaded: {len(self.crypto_pairs)} pairs (major: {len(self.major_crypto)})")
        return self.crypto_pairs

    def load_price_reference(self) -> dict[str, float]:
        """Ambil harga acuan semua pair dari CoinGecko.

        Dipakai untuk mencocokkan hasil Yahoo Finance. Namespace ticker Yahoo
        ({SYM}-USD) punya makna sendiri: simbol yang sama belum tentu aset
        yang sama. Contoh nyata: JUP-USD di Yahoo = aset lain ($0.00026)
        sementara JUP di CoinGecko = Jupiter ($0.30). Volume bukan penanda
        yang andal (candle terakhir bisa nol sementara 19 candle sebelumnya
        normal), jadi harga acuan yang dipakai sebagai pembanding.

        Semua pair diambil sekaligus, jadi biayanya 1-3 request per siklus
        scan, bukan satu request per ticker.
        """
        ids = {ticker: coin_id for ticker, coin_id in self.coingecko_ids.items() if coin_id}
        if not ids:
            return self.cg_price_ref

        ref: dict[str, float] = {}
        items = list(ids.items())
        for start in range(0, len(items), _PRICE_REF_CHUNK):
            chunk = items[start:start + _PRICE_REF_CHUNK]
            for attempt in range(3):
                try:
                    _coingecko_limiter.wait_if_needed()
                    resp = _cg_session.get(
                        'https://api.coingecko.com/api/v3/simple/price',
                        params={
                            'ids': ','.join(coin_id for _, coin_id in chunk),
                            'vs_currencies': 'usd',
                        },
                        timeout=15,
                    )
                    if resp.status_code == 200:
                        data = resp.json()
                        for ticker, coin_id in chunk:
                            usd = (data.get(coin_id) or {}).get('usd')
                            if usd:
                                ref[ticker] = float(usd)
                        break
                    if resp.status_code == 429:
                        # Rate limit: tunggu sebentar lalu coba lagi. Chunk
                        # yang gagal berarti ticker di dalamnya tidak punya
                        # acuan, jadi tidak terlindungi dari aset yang salah.
                        if attempt < 2:
                            time.sleep(exponential_backoff(attempt, base_delay=3.0, max_delay=20))
                            continue
                    logger.warning(
                        f"CoinGecko price reference chunk skipped (status {resp.status_code})")
                    break
                except Exception as e:
                    logger.warning(f"CoinGecko price reference failed: {e}")
                    break

        if ref:
            # Acuan lama diganti kalau yang baru lebih lengkap, supaya chunk
            # yang sempat gagal tidak membuat cakupan mengecil.
            if len(ref) >= len(self.cg_price_ref):
                self.cg_price_ref = ref
            logger.info(
                f"Price reference loaded for {len(ref)}/{len(ids)} pairs "
                f"(active coverage {len(self.cg_price_ref)})")
        return self.cg_price_ref

    def _ensure_price_reference(self, max_age: int = _PRICE_REF_TTL) -> dict[str, float]:
        """Pastikan acuan harga CoinGecko ada dan masih segar.

        Percobaan yang gagal tidak dihapus acuan lamanya, dan waktu percobaan
        tetap dicatat, supaya tidak ada request ulang per ticker saat
        CoinGecko sedang rate limit.
        """
        if (time.time() - self._price_ref_time) < max_age:
            return self.cg_price_ref
        self._price_ref_time = time.time()
        return self.load_price_reference()

    def get_usd_idr_rate(self):
        """Get USD to IDR conversion rate (cached 5 minutes)"""
        cached = self.usd_cache.get('usd_idr_rate')
        if cached:
            return cached

        try:
            url = "https://api.coingecko.com/api/v3/simple/price"
            params = {'ids': 'tether', 'vs_currencies': 'idr'}
            resp = _cg_session.get(url, params=params, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                rate = data.get('tether', {}).get('idr', 16000)
                self.usd_cache.set('usd_idr_rate', rate, ttl=300)
                return rate
        except Exception as e:
            logger.debug(f"USD/IDR rate fetch failed, using fallback 16000: {e}")
        return 16000

    def get_crypto_data(self, ticker: str, interval: str = '15m', period: str = '3d',
                        retry: int = 3, use_cache: bool = True):
        """Get crypto data from Yahoo Finance"""
        cache_key = f"{ticker}_{interval}_{period}"
        if use_cache:
            cached = self.cache.get(cache_key)
            if cached:
                return cached

        breaker = _circuit_breakers.get('yahoo')

        if breaker and not breaker.can_execute():
            logger.warning(f"Yahoo circuit breaker OPEN for {ticker}, skipping")
            return None

        for attempt in range(retry):
            try:
                _yahoo_limiter.wait_if_needed()

                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")

                import logging as _yf_logging
                yf_logger = _yf_logging.getLogger('yfinance')
                # Buang log "ticker ini tidak punya data" dari yfinance.
                # Dulu logger yfinance diturunkan levelnya (CRITICAL+1) lalu
                # dikembalikan lagi setelah fetch - kalau ada thread lain yang
                # fetch bersamaan, levelnya bisa ter-restore duluan dan log
                # yfinance bocor lagi ke alert admin.
                drop_filter = DropNoPriceData()
                yf_logger.addFilter(drop_filter)

                try:
                    stock = yf.Ticker(ticker)
                    hist = stock.history(interval=interval, period=period, timeout=15)

                    if hist.empty or len(hist) < 50:
                        if attempt < retry - 1:
                            time.sleep(exponential_backoff(attempt, base_delay=0.5, max_delay=4))
                            continue
                        logger.warning(
                            f"Yahoo tidak punya data untuk {ticker} "
                            f"setelah {retry} percobaan - pakai fallback")
                        return None

                    df = hist.copy()

                    # Ticker {SYM}-USD di Yahoo bisa milik aset yang sama
                    # sekali berbeda dari CoinGecko yang jadi sumber daftar
                    # pair. Contoh nyata: AERO-USD di Yahoo = "Aerovek
                    # Aviation" (token mati, harga ~$0.000025, volume 0),
                    # bukan Aerodrome Finance (~$0.78). Angka seperti ini
                    # lolos semua validasi harga/RSI karena "valid" —
                    # satu-satunya penanda adalah volume nol. Tolak supaya
                    # yang dipakai fallback CoinGecko (id yang benar).
                    if float(df['Volume'].tail(20).sum()) <= 0:
                        logger.warning(
                            f"Yahoo data for {ticker} has zero volume in the "
                            f"last 20 candles - dead/mismatched asset, rejecting")
                        return None

                    df['MA_FAST'] = df['Close'].rolling(8).mean()
                    df['MA_SLOW'] = df['Close'].rolling(21).mean()
                    df['RSI'] = calculate_rsi(df['Close'])

                    macd_data = calculate_macd(df['Close'])
                    df['MACD'] = macd_data['macd']
                    df['MACD_SIGNAL'] = macd_data['signal']
                    df['MACD_HIST'] = macd_data['histogram']

                    bb_data = calculate_bollinger_bands(df['Close'])
                    df['BB_UPPER'] = bb_data['upper']
                    df['BB_MIDDLE'] = bb_data['middle']
                    df['BB_LOWER'] = bb_data['lower']

                    volume_data = calculate_volume_metrics(df['Volume'])
                    df['VOLUME_MA'] = volume_data['ma']
                    df['VOLUME_RATIO'] = volume_data['ratio']

                    vwap_data = calculate_vwap(df['High'], df['Low'], df['Close'], df['Volume'])
                    df['VWAP'] = vwap_data['vwap']
                    df['VWAP_CURRENT'] = vwap_data['current']

                    stoch_data = calculate_stochastic(df['High'], df['Low'], df['Close'])
                    df['STOCH_K'] = stoch_data['k']
                    df['STOCH_D'] = stoch_data['d']

                    adx_data = calculate_adx(df['High'], df['Low'], df['Close'])
                    df['ADX'] = adx_data['adx']
                    df['PLUS_DI'] = adx_data['plus_di']
                    df['MINUS_DI'] = adx_data['minus_di']

                    ichimoku_data = calculate_ichimoku(df['High'], df['Low'], df['Close'])
                    fib_data = calculate_fibonacci_retracement(df['High'], df['Low'])

                    # Calculate Support & Resistance
                    try:
                        sr_data = calculate_sr_levels(df['High'], df['Low'], df['Close'], df['Volume'])
                    except Exception as e:
                        logger.debug(f"S/R calculation failed for {ticker}: {e}")
                        sr_data = {}

                    latest = df.iloc[-1]
                    price = latest['Close']

                    if price is None or price <= 0 or price > 1000000:
                        if attempt < retry - 1:
                            time.sleep(exponential_backoff(attempt, base_delay=0.5, max_delay=4))
                            continue
                        return None

                    # Cocokkan harga Yahoo dengan acuan CoinGecko. Namespace
                    # ticker Yahoo ({SYM}-USD) tidak sama dengan CoinGecko,
                    # jadi simbol yang cocok belum tentu aset yang cocok
                    # (JUP-USD di Yahoo = aset lain, bukan Jupiter). Kalau
                    # harga beda jauh, datanya milik aset yang salah - tolak
                    # supaya fallback CoinGecko yang dipakai.
                    ref = self._ensure_price_reference().get(ticker)
                    if ref and ref > 0:
                        ratio = price / ref
                        if ratio < (1 / _PRICE_MISMATCH_RATIO) or ratio > _PRICE_MISMATCH_RATIO:
                            logger.warning(
                                f"Yahoo price for {ticker} ({price:.8f}) differs from "
                                f"CoinGecko reference ({ref:.8f}) by {ratio:.1f}x - "
                                f"likely a different asset, rejecting")
                            return None

                    rsi = latest['RSI']
                    if rsi is None or rsi <= 0 or rsi > 100 or (rsi != rsi):
                        if attempt < retry - 1:
                            time.sleep(exponential_backoff(attempt, base_delay=0.5, max_delay=4))
                            continue
                        return None

                    atr_val = df['High'].diff().abs().rolling(14).mean().iloc[-1]
                    if atr_val is None or atr_val <= 0:
                        atr_val = price * 0.025

                    if breaker:
                        breaker.record_success()

                    result = {
                        'ticker': ticker,
                        'name': self.crypto_pairs.get(ticker, ticker),
                        'price': price,
                        'change': ((latest['Close'] - df.iloc[-2]['Close']) / df.iloc[-2]['Close']) * 100,
                        'ma_fast': latest['MA_FAST'],
                        'ma_slow': latest['MA_SLOW'],
                        'rsi': rsi,
                        'volume': latest['Volume'],
                        'atr': atr_val,
                        'candles': len(df),
                        'macd': latest['MACD'],
                        'macd_signal': latest['MACD_SIGNAL'],
                        'macd_hist': latest['MACD_HIST'],
                        'bb_upper': latest['BB_UPPER'],
                        'bb_middle': latest['BB_MIDDLE'],
                        'bb_lower': latest['BB_LOWER'],
                        'volume_ma': latest['VOLUME_MA'],
                        'volume_ratio': latest['VOLUME_RATIO'],
                        'vwap': vwap_data['current'],
                        'vwap_above': vwap_data['above'],
                        'stoch_k': stoch_data['k_current'],
                        'stoch_d': stoch_data['d_current'],
                        'stoch_oversold': stoch_data['oversold'],
                        'stoch_overbought': stoch_data['overbought'],
                        'stoch_bullish_cross': stoch_data['bullish_cross'],
                        'adx': adx_data['adx_current'],
                        'plus_di': adx_data['plus_di_current'],
                        'minus_di': adx_data['minus_di_current'],
                        'adx_strong': adx_data['strong_trend'],
                        'ichi_bullish': ichimoku_data['bullish_cloud'],
                        'ichi_bearish': ichimoku_data['bearish_cloud'],
                        'ichi_cloud_above': ichimoku_data['cloud_above'],
                        'ichi_tenkan': ichimoku_data['tenkan_current'],
                        'ichi_kijun': ichimoku_data['kijun_current'],
                        'fib_levels': fib_data['levels'],
                        'raw_df': df,  # For pattern detection
                        # Support & Resistance
                        'sr': sr_data or {},
                        'support': sr_data.get('nearest_support', {}).get('level') if sr_data and sr_data.get('nearest_support') else None,
                        'resistance': sr_data.get('nearest_resistance', {}).get('level') if sr_data and sr_data.get('nearest_resistance') else None,
                    }

                    if use_cache:
                        self.cache.set(cache_key, result, ttl=60)

                    return result
                finally:
                    yf_logger.removeFilter(drop_filter)

            except Exception as e:
                logger.warning(f"Yahoo attempt {attempt+1} failed for {ticker}: {e}")
                if breaker:
                    breaker.record_failure()
                if attempt < retry - 1:
                    time.sleep(exponential_backoff(attempt, base_delay=0.5, max_delay=4))
                    continue
                return None

        return None

    def get_crypto_data_combined(self, ticker: str, interval: str = '15m', period: str = '3d'):
        """Get crypto data - try Yahoo Finance first, CoinGecko as fallback"""
        # Try Yahoo Finance first (no rate limits)
        data = self.get_crypto_data(ticker, interval, period)
        if data and not math.isnan(data['rsi']) and data['rsi'] > 0:
            data['source'] = 'yahoo'
            return data

        # Fallback to CoinGecko if Yahoo fails
        data = self.get_crypto_data_coingecko(ticker)
        if data and not math.isnan(data.get('rsi', 50)) and data.get('rsi', 50) > 0:
            return data

        return None

    def get_crypto_data_coingecko(self, ticker: str, retry: int = 3, use_cache: bool = True):
        """Get crypto data from CoinGecko API"""
        cache_key = f"cg_{ticker}"
        if use_cache:
            cached = self.cache.get(cache_key)
            if cached:
                return cached

        coin_id = self.coingecko_ids.get(ticker)
        if not coin_id:
            return None

        breaker = _circuit_breakers.get('coingecko')

        if breaker and not breaker.can_execute():
            logger.warning(f"CoinGecko circuit breaker OPEN for {ticker}, skipping")
            return None

        for attempt in range(retry):
            try:
                _coingecko_limiter.wait_if_needed()

                url = f"https://api.coingecko.com/api/v3/coins/{coin_id}"
                params = {
                    'localization': 'false',
                    'tickers': 'false',
                    'community_data': 'false',
                    'developer_data': 'false',
                    'sparkline': 'true'
                }
                resp = _cg_session.get(url, params=params, timeout=10)

                if resp.status_code == 429:
                    if breaker:
                        breaker.record_failure()
                    if attempt < retry - 1:
                        time.sleep(exponential_backoff(attempt, base_delay=2.0, max_delay=60))
                        continue
                    return None

                if resp.status_code != 200:
                    if attempt < retry - 1:
                        time.sleep(exponential_backoff(attempt, base_delay=1.0, max_delay=30))
                        continue
                    return None

                data = resp.json()
                price = data['market_data']['current_price']['usd']
                if not price or price <= 0 or price > 10000000:
                    if attempt < retry - 1:
                        time.sleep(exponential_backoff(attempt, base_delay=1.0, max_delay=30))
                        continue
                    return None

                # Some tokens (e.g. FIGR_HELOC / Figure Heloc) return null for
                # price_change_percentage_24h — normalize so downstream numeric
                # comparisons never hit None.
                change_24h = data['market_data'].get('price_change_percentage_24h')
                change_24h = float(change_24h) if change_24h is not None else 0.0
                total_volume = data['market_data'].get('total_volume') or {}
                volume = total_volume.get('usd')
                volume = float(volume) if volume is not None else 0.0
                sparkline = (data['market_data'].get('sparkline_7d') or {}).get('price')

                if not sparkline or len(sparkline) < 20:
                    if attempt < retry - 1:
                        time.sleep(exponential_backoff(attempt, base_delay=1.0, max_delay=30))
                        continue
                    return None

                prices = pd.Series(sparkline)
                ma_fast = prices.rolling(8).mean().iloc[-1]
                ma_slow = prices.rolling(21).mean().iloc[-1]

                delta = prices.diff()
                gain = delta.where(delta > 0, 0).rolling(14).mean()
                loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
                rs = gain / loss
                rsi = (100 - (100 / (1 + rs))).iloc[-1]

                if math.isnan(rsi) or rsi <= 0 or rsi > 100:
                    if attempt < retry - 1:
                        time.sleep(exponential_backoff(attempt, base_delay=1.0, max_delay=30))
                        continue
                    rsi = 50 + (change_24h * 2) if change_24h else 50
                    rsi = max(1, min(99, rsi))

                tr1 = prices.diff().abs()
                tr_all = pd.concat([tr1, tr1, tr1], axis=1).max(axis=1)
                atr_val = tr_all.rolling(14).mean().iloc[-1] if len(tr_all) >= 14 else tr_all.mean()
                if not atr_val or atr_val <= 0 or atr_val < (price * 0.001):
                    atr_val = price * 0.025

                macd_data = calculate_macd(prices)
                macd = macd_data['macd'].iloc[-1]
                macd_signal = macd_data['signal'].iloc[-1]
                macd_hist = macd_data['histogram'].iloc[-1]

                bb_data = calculate_bollinger_bands(prices)
                bb_upper = bb_data['upper'].iloc[-1]
                bb_middle = bb_data['middle'].iloc[-1]
                bb_lower = bb_data['lower'].iloc[-1]

                if breaker:
                    breaker.record_success()

                result = {
                    'ticker': ticker,
                    'name': data['name'],
                    'price': price,
                    'change': change_24h,
                    'ma_fast': ma_fast,
                    'ma_slow': ma_slow,
                    'rsi': rsi,
                    'volume': volume,
                    'atr': atr_val,
                    'candles': len(sparkline),
                    'source': 'coingecko',
                    'macd': macd,
                    'macd_signal': macd_signal,
                    'macd_hist': macd_hist,
                    'bb_upper': bb_upper,
                    'bb_middle': bb_middle,
                    'bb_lower': bb_lower,
                    'volume_ma': volume,
                    'volume_ratio': 1.0,
                }

                if use_cache:
                    self.cache.set(cache_key, result, ttl=60)

                return result
            except Exception as e:
                if breaker:
                    breaker.record_failure()
                if attempt < retry - 1:
                    logger.warning(f"CoinGecko error for {ticker}: {e}, retry {attempt + 1}/{retry}")
                    time.sleep(exponential_backoff(attempt, base_delay=1.0, max_delay=30))
                    continue
                logger.error(f"CoinGecko failed for {ticker} after {retry} attempts: {e}")
                return None

        return None


# Singleton instance
crypto_service = CryptoService()
