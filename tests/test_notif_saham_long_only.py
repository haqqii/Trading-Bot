"""Saham long-only: notifikasi saham hanya BUY / TP1 / TP2 / TP3 / SL.

Sinyal JUAL (rekomendasi jual) tidak dikirim sama sekali untuk saham.
Alasannya praktis: short selling di BEI terbatas - hanya saham yang ada
di daftar short-sell dan butuh akun margin. Jadi tidak ada posisi short
yang bisa dibuat dari sinyal itu, dan notifikasi jual cuma menambah
kebisingan.

Yang dijaga di sini:
1. Scan saham tidak pernah mengirim notifikasi SELL.
2. Sinyal SELL tidak masuk daftar kandidat (jadi tidak menempati slot
   TOP 3 dan tidak memblokir sinyal BELI untuk ticker yang sama).
3. Sinyal BELI tetap dipantau: TP1/TP2/TP3/SL tetap keluar.
"""
import sys
import os
import asyncio
from datetime import datetime, timezone, timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

WIB = timezone(timedelta(hours=7))
# Rabu, 10:00 WIB - hari kerja dan jam pasar.
NOW = datetime(2026, 11, 25, 10, 0, tzinfo=WIB)


class FakeBot:
    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id, text, **kwargs):
        self.sent.append((chat_id, text))


class FakeJobQueue:
    def run_once(self, target, when=0):
        pass


class FakeApp:
    def __init__(self):
        self.bot = FakeBot()
        self.job_queue = FakeJobQueue()


def make_data(ticker):
    """Payload saham seperti hasil get_stock_data_with_fallback()."""
    return {
        'ticker': ticker,
        'price': 100.0,
        'atr': 2.0,
        'rsi': 45.0,
        'ma_fast': 101.0,
        'ma_slow': 99.0,
        'macd_hist': 0.5,
        'volume_ratio': 1.2,
        'change': 1.0,
        'bb_position': 0.5,
        'candles': 50,
        'support': 95.0,
        'resistance': 105.0,
        'sr': {},
    }


class FakeSignalService:
    """Engine palsu: arah sinyal ditentukan per ticker."""

    def __init__(self, direction_by_ticker):
        self.direction_by_ticker = direction_by_ticker

    def generate_stock_signal(self, d):
        # Ticker dari provider punya sufiks .JK - dicocokkan tanpa sufiks.
        ticker = str(d.get('ticker', '')).replace('.JK', '')
        direction = self.direction_by_ticker.get(ticker, 'BUY')
        return {
            'signal': direction,
            'entry': d['price'],
            'buy_score': 80,
            'sell_score': 80,
            'quality': 'STRONG',
            'reason': 'test',
            'macd_hist': 0.5,
            'is_reversal': False,
        }


@pytest.fixture
def scan(monkeypatch):
    """Scan saham dengan dua ticker: satu sinyal BUY, satu SELL."""
    from handlers.scheduler import jobs_stocks
    from services import signal_service as signal_service_module
    from services import ticker_health

    state = {'saved': [], 'signals': {}}

    monkeypatch.setattr(jobs_stocks, 'now_wib', lambda: NOW)
    monkeypatch.setattr(jobs_stocks, 'ALL_STOCKS', {
        'BUYCO': 'PT Buy Co Tbk',
        'SELLCO': 'PT Sell Co Tbk',
    })
    monkeypatch.setattr(ticker_health, 'is_unavailable', lambda t: False)
    monkeypatch.setattr(
        jobs_stocks, 'get_stock_data_with_fallback',
        lambda ticker, interval='5m', period='5d': (make_data(ticker), False))
    monkeypatch.setattr(signal_service_module, 'signal_service',
                        FakeSignalService({
                            'BUYCO': 'BUY',
                            'SELLCO': 'SELL',
                        }))
    monkeypatch.setattr(jobs_stocks, '_get_last_buy_signals',
                        lambda: state['signals'])
    monkeypatch.setattr(jobs_stocks, '_schedule_followup_scan',
                        lambda *a, **k: None)
    monkeypatch.setattr(jobs_stocks.db, 'get_all_signals', lambda: {})
    monkeypatch.setattr(jobs_stocks.db, 'save_active_signal',
                        lambda **kwargs: state['saved'].append(kwargs))
    monkeypatch.setattr(jobs_stocks, 'SEND_DELAY_SECONDS', 0)
    jobs_stocks._last_scan_by_tf.clear()
    jobs_stocks._force_next_scan = True
    jobs_stocks._yahoo_stock_cooldown = 0
    return state


def _run_scan():
    from handlers.scheduler import jobs_stocks

    jobs_stocks._get_user_db = lambda: {
        '1': {'notif_saham': True, 'timeframe': '60'}}
    app = FakeApp()
    asyncio.run(jobs_stocks.check_stock_signals(app))
    return app


class TestNoSellNotification:
    """Sinyal JUAL saham tidak boleh jadi notifikasi."""

    def test_sell_signal_is_not_notified(self, scan):
        app = _run_scan()
        texts = [t for _, t in app.bot.sent]
        assert not any('SELLCO' in t for t in texts), \
            'notifikasi sinyal JUAL saham terkirim'

    def test_buy_signal_is_still_notified(self, scan):
        app = _run_scan()
        texts = [t for _, t in app.bot.sent]
        assert any('BUYCO' in t for t in texts), (
            'notifikasi sinyal BELI ikut hilang - filter jangan sampai '
            'menyaring BUY')

    def test_sell_signal_is_not_saved_as_position(self, scan):
        """Sinyal yang tidak dikirim tidak boleh jadi posisi aktif -
        kalau disimpan, user tetap dapat notifikasi exit-nya."""
        _run_scan()
        saved = [s['ticker'] for s in scan['saved']]
        assert 'SELLCO' not in saved
        assert 'BUYCO' in saved

    def test_sell_signal_does_not_take_top3_slot(self, scan):
        """Sinyal jual tidak boleh menempati slot TOP 3 - kalau iya, sinyal
        beli yang sah jadi tidak terkirim."""
        app = _run_scan()
        texts = [t for _, t in app.bot.sent]
        buy_count = sum(1 for t in texts if 'BUYCO' in t)
        assert buy_count == 1, \
            f'hanya {buy_count} sinyal BELI terkirim, ada yang kena slot'


class TestSellRenderingSafetyNet:
    """Kalau fitur sinyal jual dihidupkan lagi, tampilannya harus jelas."""

    def test_sell_rendering_states_no_short_position(self):
        from utils.formatters import format_unified_stock_notification

        msg = format_unified_stock_notification(
            notif_type='SELL', ticker='SELLCO', name='PT Sell Co Tbk',
            entry=100.0, current_price=100.0, tp1=98.0, tp2=96.0, tp3=93.0,
            sl=103.0, analysis_data={'indicators': {'atr': 2.0}},
            direction='SHORT')
        assert 'tidak membuka posisi short' in msg
        assert 'REKOMENDASI JUAL' in msg


class TestSellOutcomeNotRestored:
    """Baris 'info' (sinyal info, bukan posisi) tidak dimuat jadi posisi."""

    def test_info_outcome_is_not_restored(self, tmp_path):
        import db as db_module

        original_db = db_module.db
        test_db = db_module.Database(str(tmp_path / 'signals.db'))
        db_module.db = test_db
        try:
            test_db.save_active_signal(
                key='STOCK_SELLCO_1', ticker='SELLCO', asset_type='stock',
                signal_type='SELL', price=100, tp1=98, tp2=96, tp3=93,
                sl=103, score=70, quality='STRONG', reason='x',
                extra_data={'user_id': '1', 'direction': 'SHORT',
                            'timeframe': '60'},
                outcome='info')

            restored = test_db.load_active_signals()
            assert 'STOCK_SELLCO_1' not in restored, \
                'baris info dimuat ulang jadi posisi aktif'
            # Barisnya tetap ada di DB -> cooldown & anti-duplikat tetap jalan.
            assert test_db.get_all_signals().get('STOCK_SELLCO_1')
        finally:
            db_module.db = original_db
