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
from utils.log_filters import install_provider_filters, no_data_reported
from services import crypto_yahoo_missing
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

# Batas waktu menunggu slot limiter CoinGecko untuk jalur prioritas
# (command user dan job cek TP/SL). Tanpa batas, satu slot yang lambat
# muncul bisa menahan jalur ini hampir semenit - command user jadi
# kelihatan "gabisa" dan siklus cek TP/SL bisa lewat batas 2 menit.
# Lewat batas ini, jalur prioritas memakai harga cache (basi) saja.
_COINGECKO_PRIORITY_MAX_WAIT = 20

# Umur cache data harga (detik). Scan crypto jalan tiap 15 menit, job TP/SL
# tiap 2 menit - kalau cache terlalu pendek, keduanya berebut request ke
# provider yang sama (batas Yahoo 20 request/menit, CoinGecko 5/menit) dan
# salah satunya jadi lambat atau kena 429.
_YAHOO_CACHE_TTL = 300
_COINGECKO_CACHE_TTL = 300

# Jumlah ticker per request /coins/markets (bulk CoinGecko). ID dipisah koma
# jadi URL-nya dibatasi supaya tidak terlalu panjang.
_COINGECKO_BULK_CHUNK = 100

# Berapa pair yang normalnya dipantau kalau CoinGecko sempat bisa diakses
# (250 koin teratas, setelah symbol duplikat dibuang).
_EXPECTED_PAIR_COUNT = 250

# Jeda minimum antar percobaan ambil daftar pair. Tanpa ini, container yang
# baru start langsung retry (10 detik setelah start) dan kena rate limit lagi.
_PAIR_LOAD_MIN_INTERVAL = 100


# --- Deteksi stablecoin ------------------------------------------------------
# Stablecoin = aset yang nilainya dipatok ke mata uang lain (biasanya $1).
# Rekomendasi "beli" untuk stablecoin tidak berguna: volatilitasnya ~0, jadi
# tidak ada peluang profit dan TP/SL-nya tidak pernah kena. Di produksi,
# 12 dari 54 sinyal crypto yang terkirim selama sehari adalah stablecoin -
# semuanya noise.
#
# Dua syarat, keduanya wajib:
# 1. Harga dekat peg (0.80 - 1.60). Aset biasa yang kebetulan~$1 (FIL, DOT,
#    BTSE) tidak ikut tersaring karena syarat 2 tidak terpenuhi.
# 2. Hampir tidak bergerak: rentang harga 24 jam <= 0.5%.
#
# Kenapa "rentang 24 jam", bukan lebar Bollinger band atau ATR:
# - ATR: jalur CoinGecko mengganti ATR di bawah 0.1% harga dengan 2.5%
#   (lihat get_crypto_data_coingecko), jadi stablecoin yang diambil dari
#   CoinGecko terlihat "bergerak".
# - Lebar Bollinger band: dihitung dari 20 bar, jadi lebarnya ikut
#   timeframe. Pada timeframe 1 menit, 20 bar cuma 20 menit dan aset biasa
#   pun punya band sempit - aset baik bisa ikut tersaring.
# - Rentang 24 jam selalu mengukur periode yang sama, jadi hasilnya sama
#   untuk timeframe 1 menit maupun 1 hari, dan dihitung dari seri yang sama
#   di kedua jalur data (Yahoo pakai High/Low, CoinGecko pakai sparkline).
#
# Data produksi (25 Okt 2026), rentang 24 jam:
#   stablecoin : 0.037% (USDAI), 0.14% (BFUSD), 0.23% (EURC)
#   aset biasa : 1.465% (MX), 1.80% (BTC), 4.12% (FIL), 5.38% (PIEVERSE)
# Ambang 0.5% ada di tengah celah 6x itu.
STABLECOIN_PRICE_MIN = 0.80
STABLECOIN_PRICE_MAX = 1.60
STABLECOIN_MAX_RANGE_24H = 0.5   # persen

# Stablecoin yang dipatok ke mata uang selain USD (JPY Coin, JPY Stablecoin).
# Harganya dalam USD bergerak mengikuti kurs, jadi tidak bisa dideteksi dari
# rentang harga: JPYC bergerak 14% dalam sebulan, karena yen menguat. Daftar
# ini perlu diupdate kalau CoinGecko menambah stablecoin dengan peg non-USD.
NON_USD_STABLECOINS = {
    'JPYC-USD',
    'JPYSC-USD',
}

# Interval Yahoo -> jam. Dipakai untuk menghitung berapa bar yang menutup
# 24 jam, supaya metrik "rentang 24 jam" sama untuk semua timeframe.
_INTERVAL_HOURS = {
    '1m': 1 / 60, '2m': 2 / 60, '5m': 5 / 60, '15m': 0.25,
    '30m': 0.5, '1h': 1, '2h': 2, '4h': 4, '1d': 24,
}


def compute_range_pct(high: float, low: float, price: float,
                      window_hours: float) -> float | None:
    """Rentang harga dalam persen, dinormalisasi ke window 24 jam.

    Yahoo untuk interval 1m hanya memberi ~500 bar (sekitar 8 jam), bukan
    24 jam, jadi window yang tersedia lebih pendek dari yang diinginkan.
    Pergerakan harga ~ random walk, jadi rentang naik dengan akar waktu -
    hasil window 8 jam dikali sqrt(8/24) supaya comparable dengan window
    24 jam. Tanpa ini, timeframe 1 menit akan melihat aset biasa apa pun
    sebagai "datar".
    """
    if price is None or price <= 0 or window_hours is None or window_hours <= 0:
        return None
    scale = math.sqrt(window_hours / 24.0)
    return ((high - low) / price * 100) / scale


def compute_range_24h(df, price: float, interval: str) -> float | None:
    """Rentang harga 24 jam (persen) dari frame OHLC Yahoo.

    `df` = DataFrame dengan kolom High/Low (apa adanya dari yfinance).
    """
    if df is None or price is None or price <= 0:
        return None
    bar_hours = _INTERVAL_HOURS.get(interval, 1)
    bars_24h = max(2, int(round(24 / bar_hours)))
    recent = df.tail(bars_24h)
    if len(recent) < 2:
        return None
    try:
        high = float(recent['High'].max())
        low = float(recent['Low'].min())
    except (KeyError, TypeError, ValueError):
        return None
    return compute_range_pct(high, low, price, len(recent) * bar_hours)


def is_stablecoin(data: dict) -> bool:
    """True kalau data harga menunjukkan aset yang nilainya dipatok.

    `data` = dict hasil get_crypto_data / get_crypto_data_coingecko.

    Mengembalikan False kalau metriknya tidak ada atau rusak, supaya aset
    yang tidak bisa dinilai tidak ikut tersaring diam-diam.
    """
    ticker = str(data.get('ticker') or '').upper()
    if ticker in NON_USD_STABLECOINS:
        return True

    try:
        price = float(data.get('price') or 0)
    except (TypeError, ValueError):
        return False
    if price <= 0:
        return False
    if not (STABLECOIN_PRICE_MIN <= price <= STABLECOIN_PRICE_MAX):
        return False

    range_24h = data.get('range_24h')
    if range_24h is None:
        return False
    try:
        range_24h = float(range_24h)
    except (TypeError, ValueError):
        return False

    return range_24h <= STABLECOIN_MAX_RANGE_24H


def build_crypto_payload(ticker: str, name: str, price, change_24h, volume,
                         sparkline, rsi_fallback: bool = False):
    """Bangun payload indikator crypto dari seri harga.

    Seri harga = sparkline hourly 7 hari (168 titik) dari CoinGecko. Fungsi
    ini satu-satunya tempat perhitungan indikator jalur CoinGecko, jadi jalur
    satu-per-ticker dan jalur bulk selalu menghasilkan angka yang sama.

    Args:
        rsi_fallback: kalau True, RSI yang tidak valid diganti perkiraan
            (50 + perubahan 24 jam) alih-alih dianggap gagal.

    Returns:
        (payload, error). error=None kalau payload berhasil dibuat.
        error: 'price' | 'sparkline' | 'rsi' kalau gagal.
    """
    try:
        price = float(price)
    except (TypeError, ValueError):
        return None, 'price'
    if not price or price <= 0 or price > 10000000:
        return None, 'price'

    try:
        change_24h = float(change_24h) if change_24h is not None else 0.0
    except (TypeError, ValueError):
        change_24h = 0.0
    try:
        volume = float(volume) if volume is not None else 0.0
    except (TypeError, ValueError):
        volume = 0.0

    if not sparkline or len(sparkline) < 20:
        return None, 'sparkline'

    prices = pd.Series(sparkline)
    ma_fast = prices.rolling(8).mean().iloc[-1]
    ma_slow = prices.rolling(21).mean().iloc[-1]

    delta = prices.diff()
    gain = delta.where(delta > 0, 0).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
    rs = gain / loss
    rsi = (100 - (100 / (1 + rs))).iloc[-1]

    if math.isnan(rsi) or rsi <= 0 or rsi > 100:
        if not rsi_fallback:
            return None, 'rsi'
        rsi = 50 + (change_24h * 2) if change_24h else 50
        rsi = max(1, min(99, rsi))

    tr1 = prices.diff().abs()
    tr_all = pd.concat([tr1, tr1, tr1], axis=1).max(axis=1)
    atr_val = tr_all.rolling(14).mean().iloc[-1] if len(tr_all) >= 14 else tr_all.mean()
    if not atr_val or atr_val <= 0 or atr_val < (price * 0.001):
        atr_val = price * 0.025

    macd_data = calculate_macd(prices)
    bb_data = calculate_bollinger_bands(prices)

    # Rentang harga 24 jam (persen). Sparkline CoinGecko hourly 7 hari,
    # jadi 24 jam = 24 titik terakhir. Nilai ini dibandingkan dengan hasil
    # Yahoo (yang memakai bar High/Low) supaya filter stablecoin hasilnya
    # sama untuk kedua jalur.
    spark_24h = sparkline[-24:] if len(sparkline) >= 24 else sparkline
    if len(spark_24h) >= 2:
        range_24h = compute_range_pct(max(spark_24h), min(spark_24h), price, 24.0)
    else:
        range_24h = None

    return {
        'ticker': ticker,
        'name': name,
        'price': price,
        'change': change_24h,
        'ma_fast': ma_fast,
        'ma_slow': ma_slow,
        'rsi': rsi,
        'volume': volume,
        'atr': atr_val,
        'range_24h': range_24h,
        'candles': len(sparkline),
        'source': 'coingecko',
        'macd': macd_data['macd'].iloc[-1],
        'macd_signal': macd_data['signal'].iloc[-1],
        'macd_hist': macd_data['histogram'].iloc[-1],
        'bb_upper': bb_data['upper'].iloc[-1],
        'bb_middle': bb_data['middle'].iloc[-1],
        'bb_lower': bb_data['lower'].iloc[-1],
        'volume_ma': volume,
        'volume_ratio': 1.0,
    }, None


class CryptoService:
    """Service for fetching crypto data"""

    def __init__(self):
        self.cache = _price_cache
        self.usd_cache = _usd_cache
        self.crypto_pairs = {}
        self.coingecko_ids = {}
        self.major_crypto = set()
        self._crypto_loaded = False
        # True kalau daftar pair jatuh ke daftar cadangan (12 koin) karena
        # CoinGecko sedang rate limit. Selama status ini masih ada, hanya
        # 12 aset yang dipantau - itu perlu diketahui admin.
        self.used_fallback_pairs = False
        # Berapa banyak pair yang normalnya dipantau (250 koin teratas dari
        # CoinGecko, setelah symbol duplikat dibuang). Dipakai untuk
        # memberi tahu admin seberapa jauh daftar yang aktif berkurang.
        self.expected_pair_count = _EXPECTED_PAIR_COUNT
        # Kapan terakhir kali daftar pair dicoba ambil. Dipakai supaya
        # percobaan ulang tidak langsung menumpuk di detik yang sama
        # dengan percobaan yang gagal (misal restart container).
        self._last_pair_load_time: float = 0.0
        # Harga acuan dari CoinGecko untuk mencocokkan hasil Yahoo Finance.
        self.cg_price_ref: dict[str, float] = {}
        self._price_ref_time: float = 0.0

    def load_crypto_pairs(self, force: bool = False, priority: bool = True):
        """Auto-load crypto pairs from CoinGecko (top 250 coins) with fallback

        priority=True (default): daftar pair itu fondasi bot - command user
        (/analisa) butuh tahu ticker mana yang crypto, dan job recovery
        butuh memulihkan daftar yang hilang. Jadi request-nya boleh memakai
        kuota cadangan, tidak perlu antre di belakang scan.

        Respons /coins/markets sekaligus membawa harga USD tiap koin, jadi
        daftar ini sekaligus jadi acuan harga - tanpa request CoinGecko
        tambahan.
        """
        if self._crypto_loaded and not force:
            return self.crypto_pairs

        coingecko_mappings = {}
        major_set = set()
        # Harga USD dari respons CoinGecko. Dipakai sebagai acuan harga
        # (ticker yang simbolnya sama di dua provider bisa menunjuk aset
        # yang berbeda) tanpa request tambahan.
        price_seed: dict[str, float] = {}
        cg_failed = False
        self._last_pair_load_time = time.time()

        try:
            url = "https://api.coingecko.com/api/v3/coins/markets"
            params = {
                'vs_currency': 'usd',
                'order': 'market_cap_desc',
                'per_page': 250,
                'sparkline': 'false',
                'price_change_percentage': '24h'
            }

            # Satu request saja. Dulu loop ini mengambil "halaman 1-2"
            # tapi parameter 'page' tidak pernah dikirim ke CoinGecko,
            # jadi request kedua mengulang request pertama - satu kuota
            # CoinGecko terbuang percuma setiap start.
            #
            # Halaman kedua (250 koin berikutnya) sengaja tidak diambil:
            # kuota CoinGecko free tier hanya ~4 request/menit, dan kuota
            # itu lebih penting dipakai untuk harga (acuan + cek TP/SL)
            # daripada menambah daftar koin yang dipantau.
            try:
                # Pakai limiter yang sama dengan jalur CoinGecko lain.
                # Tanpa ini, request saat start tidak ikut dihitung dan
                # bisa menabrak kuota 5 request/menit.
                _coingecko_limiter.wait_if_needed(priority=priority)
                resp = _cg_session.get(url, params=params, timeout=10)
            except Exception as req_err:
                logger.warning(f"CoinGecko request failed: {req_err}")
                cg_failed = True
                resp = None

            if resp is not None:
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
                            # Respons /coins/markets sudah membawa harga
                            # sekarang, jadi sekalian dipakai sebagai
                            # acuan harga - hemat 1-3 request CoinGecko.
                            price = coin.get('current_price')
                            if isinstance(price, (int, float)) and price > 0:
                                price_seed[ticker] = float(price)
                elif resp.status_code == 429:
                    # Rate limited - skip immediately, use fallback
                    logger.warning(f"CoinGecko rate limited, skipping to fallback")
                    cg_failed = True
                else:
                    logger.warning(f"CoinGecko error: {resp.status_code}")
                    cg_failed = True

            logger.info(f"CoinGecko: fetched {len(coingecko_mappings)} coins")
        except Exception as e:
            logger.warning(f"CoinGecko fetch failed: {e}, falling back to hardcoded list")
            cg_failed = True

        # Acuan harga diisi dari respons yang sama. Tanpa ini, acuan harus
        # dimuat terpisah (1-3 request CoinGecko lagi) - di saat start itu
        # berarti start bisa kena 429 dan acuan harga kosong selama 10
        # menit, sehingga ticker yang simbolnya sama di dua provider
        # bisa dipakai untuk aset yang salah.
        if price_seed:
            merged = dict(self.cg_price_ref)
            merged.update(price_seed)
            if len(merged) >= len(self.cg_price_ref):
                self.cg_price_ref = merged
                self._price_ref_time = time.time()
            logger.info(f"Acuan harga awal {len(price_seed)} pair dari respons "
                        f"CoinGecko (total {len(self.cg_price_ref)})")

        # Fallback if CoinGecko failed or returned too few coins
        used_fallback = cg_failed or len(coingecko_mappings) < 10
        if used_fallback:
            logger.warning(f"Using fallback crypto list ({len(_FALLBACK_CRYPTO_PAIRS)} coins)")
            # Merge with what we got from CoinGecko (prefer CoinGecko data)
            for ticker, value in _FALLBACK_CRYPTO_PAIRS.items():
                if ticker not in coingecko_mappings:
                    coingecko_mappings[ticker] = value

        self.crypto_pairs = {ticker: name for ticker, (name, _) in coingecko_mappings.items()}
        self.coingecko_ids = {ticker: cg_id for ticker, (_, cg_id) in coingecko_mappings.items()}
        self.major_crypto = major_set & set(self.crypto_pairs.keys())
        self.used_fallback_pairs = used_fallback
        if not used_fallback:
            # Simpan jumlah pair dari daftar lengkap, supaya kalau nanti
            # jatuh ke daftar cadangan admin tahu bedanya berapa.
            self.expected_pair_count = len(self.crypto_pairs)
        self._crypto_loaded = True
        logger.info(f"Crypto loaded: {len(self.crypto_pairs)} pairs (major: {len(self.major_crypto)})")
        return self.crypto_pairs

    def retry_load_crypto_pairs(self) -> bool:
        """Coba lagi ambil daftar pair kalau sebelumnya jatuh ke daftar cadangan.

        Tanpa retry, CoinGecko yang kena rate limit saat start bikin bot
        hanya memantau 12 koin sampai container di-restart. Dipanggil
        berkala dari job prefetch supaya daftar pulih sendiri.

        Returns:
            True kalau sekarang daftar lengkap (tidak lagi fallback).
        """
        if not self.used_fallback_pairs:
            return True

        # Jangan langsung retry kalau percobaan terakhir baru saja terjadi.
        # Container yang baru start memang memanggil job prefetch ini dalam
        # hitungan detik - percobaan kedua secepat itu hanya kena rate limit
        # lagi dan wasting kuota.
        since_last = time.time() - self._last_pair_load_time
        if 0 < since_last < _PAIR_LOAD_MIN_INTERVAL:
            logger.info(
                f"Belum coba ulang daftar pair crypto (percobaan terakhir "
                f"{since_last:.0f} detik lalu, minimal "
                f"{_PAIR_LOAD_MIN_INTERVAL} detik)")
            return False

        logger.info("Mencoba lagi ambil daftar pair crypto "
                    "(sebelumnya jatuh ke daftar cadangan)")
        try:
            self.load_crypto_pairs(force=True)
        except Exception as e:
            logger.warning(f"Gagal ambil ulang daftar pair crypto: {e}")
            return False

        if self.used_fallback_pairs:
            logger.warning("CoinGecko masih rate limited - daftar pair crypto "
                           "masih pakai daftar cadangan")
            return False

        logger.info(f"Daftar pair crypto pulih: {len(self.crypto_pairs)} pair")
        return True

    def load_price_reference(self, priority: bool = False) -> dict[str, float]:
        """Ambil harga acuan semua pair dari CoinGecko.

        Dipakai untuk mencocokkan hasil Yahoo Finance. Namespace ticker Yahoo
        ({SYM}-USD) punya makna sendiri: simbol yang sama belum tentu aset
        yang sama. Contoh nyata: JUP-USD di Yahoo = aset lain ($0.00026)
        sementara JUP di CoinGecko = Jupiter ($0.30). Volume bukan penanda
        yang andal (candle terakhir bisa nol sementara 19 candle sebelumnya
        normal), jadi harga acuan yang dipakai sebagai pembanding.

        Semua pair diambil sekaligus, jadi biayanya 1-3 request per siklus
        scan, bukan satu request per ticker.

        priority=True: jalur yang butuh cepat (startup) - boleh memakai
        kuota CoinGecko yang dicadangkan.
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
                    # Batas tunggu: kalau slot CoinGecko tidak ada dalam
                    # 60 detik, chunk ini dilewati (chunk sebelumnya masih
                    # jadi acuan). Tanpa batas, refresh acuan bisa menahan
                    # scan hampir 3 menit (3 chunk x hampir semenit).
                    if not _coingecko_limiter.wait_if_needed(
                            priority=priority, max_wait=60):
                        logger.warning(
                            "CoinGecko tidak ada slot dalam 60 detik - "
                            "acuan harga tidak disegarkan untuk chunk ini")
                        break
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

    def _ensure_price_reference(self, max_age: int = _PRICE_REF_TTL,
                                priority: bool = False) -> dict[str, float]:
        """Pastikan acuan harga CoinGecko ada dan masih segar.

        Percobaan yang gagal tidak dihapus acuan lamanya, dan waktu percobaan
        tetap dicatat, supaya tidak ada request ulang per ticker saat
        CoinGecko sedang rate limit.
        """
        if (time.time() - self._price_ref_time) < max_age:
            return self.cg_price_ref
        self._price_ref_time = time.time()
        ref = self.load_price_reference(priority=priority)
        if not ref:
            # Pemuatan gagal: coba lagi dalam 60 detik, bukan menunggu
            # TTL penuh. Kalau provider sedang rate limited, reload tiap
            # fetch hanya membuang kuota tanpa hasil.
            self._price_ref_time = time.time() - (max_age - 60)
        return ref

    def refresh_price_reference(self, priority: bool = True) -> dict[str, float]:
        """Muat ulang acuan harga CoinGecko kalau sudah basi.

        Dipanggil dari startup (priority=True - fondasi bot, ticker dengan
        simbol sama di dua provider bisa tertukar) dan dari scan
        (priority=False - jalan di belakang, tidak boleh-automatis
        mengambil kuota yang dipakai job cek TP/SL).

        Karena itu setiap fetch ticker cukup membandingkan dengan acuan
        yang ada, tanpa perlu memicu request CoinGecko di tengah jalan.
        """
        return self._ensure_price_reference(priority=priority)

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
                        retry: int = 3, use_cache: bool = True,
                        skip_if_missing: bool = False,
                        priority: bool = False):
        """Get crypto data from Yahoo Finance.

        skip_if_missing=True: kalau ticker ini sudah diketahui tidak ada di
        Yahoo (services/crypto_yahoo_missing.py), langsungreturn None tanpa
        request. Dipakai jalur background (scan, TP/SL); jalur yang dipicu
        user tetap mencoba, supaya ticker yang tadinya hilang bisa muncul
        lagi tanpa perlu tunggu TTL.

        priority=True: jalur yang dipicu user. Request-nya boleh pakai
        sisa kuota limiter Yahoo yang dicadangkan untuk user, jadi tidak
        antre di belakang scan yang sedang jalan.
        """
        cache_key = f"{ticker}_{interval}_{period}"
        if use_cache:
            cached = self.cache.get(cache_key)
            if cached:
                return cached

        if skip_if_missing and crypto_yahoo_missing.is_missing(ticker, interval):
            # Level INFO (bukan debug) supaya jelas di bot.log ticker mana
            # yang lewat jalur Yahoo - jadi gampang dicek tanpa masuk container.
            logger.info(
                f"Yahoo dilewati untuk {ticker} ({interval}) - ticker ini "
                f"tidak ada di Yahoo, pakai CoinGecko")
            return None

        breaker = _circuit_breakers.get('yahoo')

        if breaker and not breaker.can_execute():
            logger.warning(f"Yahoo circuit breaker OPEN for {ticker}, skipping")
            return None

        for attempt in range(retry):
            try:
                _yahoo_limiter.wait_if_needed(priority=priority)

                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")

                # Buang log "ticker ini tidak punya data" dari yfinance.
                # Dulu logger yfinance diturunkan levelnya (CRITICAL+1) lalu
                # dikembalikan lagi setelah fetch - kalau ada thread lain yang
                # fetch bersamaan, levelnya bisa ter-restore duluan dan log
                # yfinance bocor lagi ke alert admin.
                # Filter-nya dipasang sekali di startup (utils/log_filters.py),
                # jadi tidak ada balapan pasang-lepas tiap request.
                install_provider_filters()
                since = time.time()

                stock = yf.Ticker(ticker)
                hist = stock.history(interval=interval, period=period, timeout=15)

                if hist.empty:
                    # Frame kosong dua arti: ticker memang tidak ada di
                    # Yahoo, atau request-nya gagal (timeout, rate limit).
                    # Yang boleh dicatat hanya yang pertama - kalau request
                    # yang gagal, ticker-nya tetap sah dan harus dicoba lagi
                    # di scan berikutnya.
                    if attempt < retry - 1:
                        time.sleep(exponential_backoff(attempt, base_delay=0.5, max_delay=4))
                        continue
                    if no_data_reported(ticker, since):
                        # Yahoo sendiri yang bilang ticker ini tidak ada
                        # datanya. Dicatat, supaya scan berikutnya tidak
                        # menebak lagi - datanya tetap dari CoinGecko.
                        crypto_yahoo_missing.mark_missing(
                            ticker, interval,
                            reason=f'Yahoo tidak punya data untuk {ticker}',
                            universe_size=len(self.crypto_pairs))
                        logger.warning(
                            f"Yahoo tidak punya data untuk {ticker} ({interval}) "
                            f"- pakai fallback CoinGecko")
                    else:
                        logger.warning(
                            f"Yahoo tidak mengembalikan data untuk {ticker} "
                            f"({interval}) - pakai fallback CoinGecko")
                    return None

                if len(hist) < 50:
                    # Candle-nya ada tapi kurang dari 50 - ticker baru
                    # listing, bukan ticker yang hilang. Tidak ditandai.
                    #
                    # Tidak dicoba ulang: candle yang memang segini tidak
                    # akan bertambah dalam hitungan detik, dan tiap percobaan
                    # menahan jalur ini belasan detik. Pada 25 Okt 2026 satu
                    # ticker (MNT-USD) bikin tahap Yahoo di job cek TP/SL
                    # memakan 59 detik karena 3 percobaan sia-sia.
                    logger.warning(
                        f"Yahoo cuma punya {len(hist)} candle untuk {ticker} "
                        f"({interval}) - pakai fallback")
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
                #
                # Acuan dipakai apa adanya. Memuat ulang acuan = 1-3
                # request CoinGecko, dan itu kerjaan scan (yang memuatnya
                # sekali per siklus) atau startup - bukan tiap fetch
                # ticker, karena satu pemuatan bisa menahan jalur ini
                # hampir semenit di antrean limiter CoinGecko.
                ref = self.cg_price_ref.get(ticker)
                if ref and ref > 0:
                    ratio = price / ref
                    if ratio < (1 / _PRICE_MISMATCH_RATIO) or ratio > _PRICE_MISMATCH_RATIO:
                        # Kalau harga Yahoo jauh lebih kecil dari acuan,
                        # tampilkan jadi berapa kali lebih kecil - kalau
                        # ditulis sebagai rasio, hasilnya "0.0x" dan
                        # tidak informatif.
                        if ratio < 1:
                            factor = 1 / ratio
                            side = "smaller than"
                        else:
                            factor = ratio
                            side = "bigger than"
                        logger.warning(
                            f"Yahoo price for {ticker} ({price:.8f}) is {factor:.1f}x "
                            f"{side} CoinGecko reference ({ref:.8f}) - "
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

                # Rentang harga 24 jam (persen). Metrik ini dipakai untuk
                # membedakan aset yang dipatok (stablecoin) dari aset biasa,
                # dan sengaja berbasis waktu - bukan jumlah bar. Kalau
                # berbasis bar, hasilnya berbeda antara timeframe 1 menit
                # dan 4 jam, jadi aset biasa bisa ikut tersaring.
                range_24h = compute_range_24h(df, price, interval)

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
                    'range_24h': range_24h,
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
                    self.cache.set(cache_key, result, ttl=_YAHOO_CACHE_TTL)

                return result

            except Exception as e:
                logger.warning(f"Yahoo attempt {attempt+1} failed for {ticker}: {e}")
                if breaker:
                    breaker.record_failure()
                if attempt < retry - 1:
                    time.sleep(exponential_backoff(attempt, base_delay=0.5, max_delay=4))
                    continue
                return None

        return None

    def get_crypto_data_combined(self, ticker: str, interval: str = '15m', period: str = '3d',
                                 skip_if_missing: bool = False,
                                 priority: bool = False):
        """Get crypto data - try Yahoo Finance first, CoinGecko as fallback.

        skip_if_missing=True: jalur Yahoo dilewati untuk ticker yang sudah
        diketahui tidak ada di Yahoo (lihat services/crypto_yahoo_missing.py).

        priority=True: jalur yang dipicu user (lihat get_crypto_data).
        """
        # Try Yahoo Finance first (no rate limits)
        data = self.get_crypto_data(ticker, interval, period,
                                    skip_if_missing=skip_if_missing,
                                    priority=priority)
        if data and not math.isnan(data['rsi']) and data['rsi'] > 0:
            data['source'] = 'yahoo'
            return data

        # Fallback to CoinGecko if Yahoo fails
        data = self.get_crypto_data_coingecko(ticker, priority=priority)
        if data and not math.isnan(data.get('rsi', 50)) and data.get('rsi', 50) > 0:
            return data

        return None

    def get_crypto_data_coingecko(self, ticker: str, retry: int = 3, use_cache: bool = True,
                                  priority: bool = False):
        """Get crypto data from CoinGecko API

        priority=True: jalur yang butuh cepat (job cek TP/SL, command user) -
        boleh memakai kuota CoinGecko yang dicadangkan, jadi tidak menunggu
        hampir semenit di belakang request scan/prefetch.
        """
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
                if priority:
                    # Jalur yang dipicu user (dan job cek TP/SL) tidak boleh
                    # menunggu hampir semenit di antrean limiter. Kalau slot
                    # tidak ada dalam batas waktu, pakai harga cache yang
                    # basi - lebih baik daripada diam terlalu lama.
                    if not _coingecko_limiter.wait_if_needed(
                            priority=True, max_wait=_COINGECKO_PRIORITY_MAX_WAIT):
                        logger.warning(
                            f"CoinGecko tidak ada slot dalam "
                            f"{_COINGECKO_PRIORITY_MAX_WAIT} detik untuk "
                            f"{ticker} - pakai harga cache")
                        return self.cache.get_stale(cache_key)
                else:
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
                market = data.get('market_data') or {}
                price = (market.get('current_price') or {}).get('usd')

                # Some tokens (e.g. FIGR_HELOC / Figure Heloc) return null for
                # price_change_percentage_24h — normalize so downstream numeric
                # comparisons never hit None.
                change_24h = market.get('price_change_percentage_24h')
                volume = (market.get('total_volume') or {}).get('usd')
                sparkline = (market.get('sparkline_7d') or {}).get('price')

                payload, error = build_crypto_payload(
                    ticker, data.get('name') or ticker, price, change_24h,
                    volume, sparkline, rsi_fallback=(attempt == retry - 1))
                if payload is None:
                    if attempt < retry - 1:
                        time.sleep(exponential_backoff(attempt, base_delay=1.0, max_delay=30))
                        continue
                    return None

                if breaker:
                    breaker.record_success()

                if use_cache:
                    self.cache.set(cache_key, payload, ttl=_COINGECKO_CACHE_TTL)

                return payload
            except Exception as e:
                if breaker:
                    breaker.record_failure()
                if attempt < retry - 1:
                    logger.warning(f"CoinGecko error for {ticker}: {e}, retry {attempt + 1}/{retry}")
                    time.sleep(exponential_backoff(attempt, base_delay=1.0, max_delay=30))
                    continue
                logger.error(f"CoinGecko failed for {ticker} after {retry} attempts: {type(e).__name__}: {e}")
                return None

        return None

    def get_crypto_data_coingecko_bulk(self, tickers, retry: int = 3,
                                       use_cache: bool = True,
                                       priority: bool = False):
        """Ambil data CoinGecko untuk banyak ticker sekaligus.

        Endpoint /coins/markets bisa membawa dozens ticker (dengan sparkline
        7 hari) dalam satu request. Tanpa ini, scan crypto mengambil satu
        request per ticker dan rate limit CoinGecko (5 request/menit)
        menentukan lamanya scan - 83 ticker butuh 16 menit.

        Ticker yang datanya sudah ada di cache tidak diambil lagi, tapi
        tetap dikembalikan supaya pemanggil tidak mengira datanya hilang.

        priority=True: jalur yang butuh cepat (job cek TP/SL) - boleh memakai
        kuota CoinGecko yang dicadangkan, jadi tidak menunggu hampir
        semenit di belakang request scan/prefetch.

        Returns:
            dict ticker -> payload (hanya ticker yang datanya valid).
        """
        if not tickers:
            return {}

        out: dict[str, dict] = {}
        wanted: list[tuple[str, str]] = []
        for ticker in tickers:
            coin_id = self.coingecko_ids.get(ticker)
            if not coin_id:
                continue
            if use_cache:
                cached = self.cache.get(f"cg_{ticker}")
                if cached:
                    out[ticker] = cached
                    continue
            wanted.append((ticker, coin_id))

        if not wanted:
            return out

        breaker = _circuit_breakers.get('coingecko')
        if breaker and not breaker.can_execute():
            logger.warning("CoinGecko circuit breaker OPEN, bulk fetch dilewati")
            return out

        for start in range(0, len(wanted), _COINGECKO_BULK_CHUNK):
            chunk = wanted[start:start + _COINGECKO_BULK_CHUNK]
            id_to_ticker = {coin_id: ticker for ticker, coin_id in chunk}

            for attempt in range(retry):
                try:
                    if priority:
                        # Jalur prioritas tidak menunggu hampir semenit.
                        # Kalau slot tidak ada dalam batas waktu, pakai
                        # harga cache yang basi untuk sisa ticker.
                        if not _coingecko_limiter.wait_if_needed(
                                priority=True,
                                max_wait=_COINGECKO_PRIORITY_MAX_WAIT):
                            logger.warning(
                                "CoinGecko tidak ada slot dalam "
                                f"{_COINGECKO_PRIORITY_MAX_WAIT} detik - "
                                "pakai harga cache")
                            for pending, _ in wanted:
                                stale = self.cache.get_stale(f"cg_{pending}")
                                if stale:
                                    out[pending] = stale
                            return out
                    else:
                        _coingecko_limiter.wait_if_needed()

                    resp = _cg_session.get(
                        'https://api.coingecko.com/api/v3/coins/markets',
                        params={
                            'vs_currency': 'usd',
                            'ids': ','.join(coin_id for _, coin_id in chunk),
                            'sparkline': 'true',
                            'price_change_percentage': '24h',
                            'per_page': 250,
                            'page': 1,
                        },
                        timeout=25,
                    )

                    if resp.status_code == 429:
                        if breaker:
                            breaker.record_failure()
                        if attempt < retry - 1:
                            time.sleep(exponential_backoff(attempt, base_delay=2.0, max_delay=60))
                            continue
                        break

                    if resp.status_code != 200:
                        if attempt < retry - 1:
                            time.sleep(exponential_backoff(attempt, base_delay=1.0, max_delay=30))
                            continue
                        break

                    coins = resp.json()
                    if not isinstance(coins, list):
                        break

                    for coin in coins:
                        ticker = id_to_ticker.get(coin.get('id'))
                        if not ticker:
                            continue
                        sparkline = (coin.get('sparkline_in_7d') or {}).get('price')
                        payload, _ = build_crypto_payload(
                            ticker,
                            coin.get('name') or ticker,
                            coin.get('current_price'),
                            coin.get('price_change_percentage_24h'),
                            coin.get('total_volume'),
                            sparkline,
                            rsi_fallback=True,
                        )
                        if not payload:
                            continue
                        out[ticker] = payload
                        if use_cache:
                            self.cache.set(f"cg_{ticker}", payload,
                                           ttl=_COINGECKO_CACHE_TTL)

                    if breaker:
                        breaker.record_success()
                    break
                except Exception as e:
                    if breaker:
                        breaker.record_failure()
                    if attempt < retry - 1:
                        logger.warning(
                            f"CoinGecko bulk error: {type(e).__name__}: {e}, "
                            f"retry {attempt + 1}/{retry}")
                        time.sleep(exponential_backoff(attempt, base_delay=1.0, max_delay=30))
                        continue
                    logger.warning(
                        f"CoinGecko bulk gagal setelah {retry} percobaan: "
                        f"{type(e).__name__}: {e}")
                    break

        logger.info(f"CoinGecko bulk: {len(out)}/{len(wanted)} ticker "
                    f"diambil dalam {len(wanted) // _COINGECKO_BULK_CHUNK + 1} chunk")
        return out

    def get_crypto_data_coingecko_cached(self, tickers):
        """Data CoinGecko untuk banyak ticker, diambil HANYA dari cache.

        Tidak ada request ke provider. Dipakai kalau limiter CoinGecko
        sedang penuh dan menunggu slot akan terlalu lama - harga di
        cache sudah dari 5 menit terakhir, dan harga basi hanya bisa
        membuat TP/SL kelewat (bukan memicu TP/SL palsu).
        """
        out: dict[str, dict] = {}
        for ticker in tickers:
            cached = self.cache.get(f"cg_{ticker}")
            if cached:
                out[ticker] = cached
        return out


# Singleton instance
crypto_service = CryptoService()
