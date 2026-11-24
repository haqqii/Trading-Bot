"""Notifikasi harus membedakan posisi LONG (beli) dan SHORT (jual).

Kasus yang jadi pemicu: sinyal SHORT BELI (PT Global Digital Niaga Tbk)
men billed "SAHAM TP3 TERCAPAI" padahal entry 238 dan harga turun ke 198.
Ternyata itu benar - TP3-nya posisi SHORT (harga harus turun), tapi
notifikasinya ditulis dengan konvensi LONG:

- SL short di ATAS entry ditulis "+22.7%" -> terbaca sebagai gain
- target short di BAWAH entry ditulis "-16.7%" -> terbaca sebagai rugi
- tidak ada satupun penanda bahwa ini posisi jual

Akibatnya user mengira TP3-nya salah.

Keputusan akhirnya: saham jadi long-only (lihat
tests/test_notif_saham_long_only.py) - short selling di BEI terbatas, jadi
bot tidak membuka posisi short dan sinyal JUAL saham tidak dinotifikasi
sama sekali. Crypto tetap pakai short (short di crypto itu wajar).

Yang dijaga di sini:
1. Posisi short saham yang terlanjur tercatat ditutup tanpa notifikasi.
2. Posisi LONG (beli) tidak berubah: TP1/TP2/TP3/SL tetap seperti biasa.
3. Crypto: arah posisi selalu ditulis, SL jadi "risiko", TP3 menampilkan
   P/L realisasi - bukan rencana level.
"""
import sys
import os
import asyncio
from datetime import datetime, timezone, timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

WIB = timezone(timedelta(hours=7))


def render_stock(notif_type='TP3', entry=100.0, current=107.0,
                 tp1=102.0, tp2=104.0, tp3=107.0, sl=97.0,
                 direction='LONG', profit_loss=7.0):
    from utils.formatters import format_unified_stock_notification

    return format_unified_stock_notification(
        notif_type=notif_type,
        ticker='BBCA',
        name='Bank Central Asia',
        entry=entry,
        current_price=current,
        tp1=tp1, tp2=tp2, tp3=tp3, sl=sl,
        analysis_data={'indicators': {'atr': entry * 0.02}},
        change_pct=profit_loss,
        profit_loss=profit_loss,
        direction=direction,
    )


class TestLongStockNotification:
    """Posisi LONG (beli) tidak boleh berubah apa pun."""

    def test_long_has_no_short_tag(self):
        msg = render_stock(notif_type='TP3', entry=100.0, current=107.0,
                           tp1=102.0, tp2=104.0, tp3=107.0, sl=97.0,
                           direction='LONG', profit_loss=7.0)
        assert 'SHORT/JUAL' not in msg
        assert 'Posisi SHORT' not in msg

    def test_long_keeps_plan_line(self):
        msg = render_stock(notif_type='TP1', entry=100.0, current=102.0,
                           tp1=102.0, tp2=104.0, tp3=107.0, sl=97.0,
                           direction='LONG', profit_loss=2.0)
        assert 'Gain' in msg
        assert '-3.0% → +7.0%' in msg

    def test_long_tp3_shows_realized_pl(self):
        msg = render_stock(notif_type='TP3', entry=100.0, current=107.0,
                           tp1=102.0, tp2=104.0, tp3=107.0, sl=97.0,
                           direction='LONG', profit_loss=7.0)
        assert 'P/L' in msg
        assert '+7.00%' in msg


class TestShortCryptoTp3:
    """Crypto TP3 punya masalah yang sama - arahnya belum dibaca."""

    def test_crypto_short_tp3_shows_realized_pl(self):
        from utils.formatters import format_unified_crypto_notification

        msg = format_unified_crypto_notification(
            notif_type='TP3', ticker='RUNE-USD', name='THORChain',
            entry=100.0, current_price=93.0, tp1=98.0, tp2=96.0, tp3=93.0,
            sl=103.0, analysis_data={'indicators': {'atr': 1.0}},
            change_pct=7.0, profit_loss=7.0, usd_idr_rate=16000)
        assert 'P/L' in msg
        assert '+7.00%' in msg

    def test_crypto_short_tp3_sl_shown_as_risk(self):
        from utils.formatters import format_unified_crypto_notification

        msg = format_unified_crypto_notification(
            notif_type='TP3', ticker='RUNE-USD', name='THORChain',
            entry=100.0, current_price=93.0, tp1=98.0, tp2=96.0, tp3=93.0,
            sl=103.0, analysis_data={'indicators': {'atr': 1.0}},
            change_pct=7.0, profit_loss=7.0, usd_idr_rate=16000)
        assert 'risiko 3.0%' in msg

    def test_crypto_short_tp3_header_states_short(self):
        from utils.formatters import format_unified_crypto_notification

        msg = format_unified_crypto_notification(
            notif_type='TP3', ticker='RUNE-USD', name='THORChain',
            entry=100.0, current_price=93.0, tp1=98.0, tp2=96.0, tp3=93.0,
            sl=103.0, analysis_data={'indicators': {'atr': 1.0}},
            change_pct=7.0, profit_loss=7.0, usd_idr_rate=16000)
        assert 'SHORT/JUAL' in msg
        assert 'Posisi SHORT' in msg

    def test_crypto_long_tp3_has_no_short_tag(self):
        from utils.formatters import format_unified_crypto_notification

        msg = format_unified_crypto_notification(
            notif_type='TP3', ticker='RUNE-USD', name='THORChain',
            entry=100.0, current_price=107.0, tp1=102.0, tp2=104.0, tp3=107.0,
            sl=97.0, analysis_data={'indicators': {'atr': 1.0}},
            change_pct=7.0, profit_loss=7.0, usd_idr_rate=16000)
        assert 'SHORT/JUAL' not in msg
        assert 'Posisi SHORT' not in msg

    def test_crypto_short_sl_states_direction(self):
        """SL short = harga naik melewati stop. Arahnya harus ditulis."""
        from utils.formatters import format_unified_crypto_notification

        msg = format_unified_crypto_notification(
            notif_type='SL', ticker='RUNE-USD', name='THORChain',
            entry=100.0, current_price=103.0, tp1=98.0, tp2=96.0, tp3=93.0,
            sl=103.0, analysis_data={'indicators': {'atr': 1.0}},
            change_pct=3.0, profit_loss=-3.0, usd_idr_rate=16000)
        assert 'SHORT/JUAL' in msg
        assert 'Posisi SHORT' in msg
        assert '-3.00%' in msg


class FakeBot:
    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id, text, **kwargs):
        self.sent.append((chat_id, text))


class FakeJobQueue:
    def __init__(self):
        self.scheduled = []

    def run_once(self, target, when=0):
        self.scheduled.append((target, when))


class FakeApp:
    def __init__(self):
        self.bot = FakeBot()
        self.job_queue = FakeJobQueue()


def make_short_stock_signal(**overrides):
    """Sinyal saham SHORT: entry 100, ATR 1, TF 60 (swing).

    TP/SL dihitung ulang dari ATR saat cek: sl=103, tp1=98, tp2=96,
    tp3=93.
    """
    signal = {
        'name': 'PT Global Digital Niaga Tbk',
        'entry': 100.0,
        'tp1': 98.0, 'tp2': 96.0, 'tp3': 93.0, 'sl': 103.0,
        'time': None,
        'tp_hit': {'tp1': False, 'tp2': False, 'tp3': False},
        'type': 'stock',
        'direction': 'SHORT',
        'ticker_raw': 'BELI',
        'buy_score': 0, 'sell_score': 70,
        'quality': 'STRONG',
        'signal_type': 'SELL',
        'is_reversal': False,
        'atr': 1.0,
        'user_id': '1',
        'timeframe': '60',
    }
    signal.update(overrides)
    return signal


@pytest.fixture
def stock_loop(monkeypatch):
    """Loop TP/SL saham dengan harga palsu."""
    from handlers.scheduler import jobs_stocks

    state = {'signals': {}, 'outcomes': [], 'price': 93.0}

    def fake_fetch(ticker, interval, period):
        return {'price': state['price'], 'candles': 50}

    def fake_save_outcome(key, outcome, **kwargs):
        state['outcomes'].append((key, outcome))

    rabu = datetime(2026, 10, 21, 10, 0, tzinfo=WIB)
    monkeypatch.setattr(jobs_stocks, 'now_wib', lambda: rabu)
    monkeypatch.setattr(
        jobs_stocks.stock_service, 'get_stock_data_combined', fake_fetch)
    monkeypatch.setattr(jobs_stocks.db, 'save_signal_outcome', fake_save_outcome)
    monkeypatch.setattr(jobs_stocks.db, 'update_signal_extra',
                        lambda key, extra: True)
    monkeypatch.setattr(jobs_stocks, '_get_user_db', lambda: {
        '1': {'notif_saham': True, 'timeframe': '60'}})
    monkeypatch.setattr(jobs_stocks, '_get_last_buy_signals',
                        lambda: state['signals'])
    monkeypatch.setattr(jobs_stocks, '_remove_signal', lambda key: None)
    return state


class TestStockShortPositionClosedSilently:
    """Posisi SHORT saham sisa versi lama ditutup tanpa notifikasi.

    Saham jadi long-only (short selling BEI terbatas). Posisi short yang
    terlanjur tercatat ditutup diam-diam: posisinya tidak pernah ada di
    bursa user, jadi notifikasi exit-nya cuma membingungkan.
    """

    def test_short_position_is_closed_without_notification(self, stock_loop):
        from handlers.scheduler import jobs_stocks

        stock_loop['signals'] = {'STOCK_BELI_1': make_short_stock_signal()}
        app = FakeApp()
        asyncio.run(jobs_stocks.check_stock_tp_sl(app))

        assert app.bot.sent == [], \
            'notifikasi exit terkirim untuk posisi short saham'
        assert stock_loop['outcomes'] == [('STOCK_BELI_1', 'invalid')]

    def test_short_position_is_removed_from_tracking(self, stock_loop):
        from handlers.scheduler import jobs_stocks

        stock_loop['signals'] = {'STOCK_BELI_1': make_short_stock_signal()}
        app = FakeApp()
        asyncio.run(jobs_stocks.check_stock_tp_sl(app))

        assert 'STOCK_BELI_1' not in stock_loop['signals']

    def test_long_position_still_notified(self, stock_loop):
        """Posisi LONG (beli) tetap dipantau dan dinotifikasi."""
        from handlers.scheduler import jobs_stocks

        stock_loop['price'] = 102.0
        stock_loop['signals'] = {'STOCK_BBCA_1': make_short_stock_signal(
            direction='LONG', signal_type='BUY', ticker_raw='BBCA',
            entry=100.0, tp1=102.0, tp2=104.0, tp3=107.0, sl=97.0,
            buy_score=70, sell_score=0)}
        app = FakeApp()
        asyncio.run(jobs_stocks.check_stock_tp_sl(app))

        assert len(app.bot.sent) == 1
        assert 'TP1' in app.bot.sent[0][1]
        assert stock_loop['outcomes'] == [('STOCK_BBCA_1', 'tp1')]


class TestSellSignalNotRestoredAsPosition:
    """Sinyal JUAL disimpan untuk cooldown, tapi bukan posisi aktif."""

    def test_info_outcome_is_not_restored(self, tmp_path):
        import db as db_module

        original_db = db_module.db
        test_db = db_module.Database(str(tmp_path / 'signals.db'))
        db_module.db = test_db
        try:
            test_db.save_active_signal(
                key='STOCK_BELI_1', ticker='BELI', asset_type='stock',
                signal_type='SELL', price=238, tp1=211, tp2=198, tp3=198,
                sl=292, score=70, quality='STRONG', reason='x',
                extra_data={'user_id': '1', 'direction': 'SHORT',
                            'timeframe': '60'},
                outcome='info')

            restored = test_db.load_active_signals()
            assert 'STOCK_BELI_1' not in restored, \
                'sinyal jual dimuat ulang jadi posisi aktif'
            # Barisnya tetap ada di DB -> cooldown & anti-duplikat tetap jalan.
            assert test_db.get_all_signals().get('STOCK_BELI_1')
        finally:
            db_module.db = original_db
