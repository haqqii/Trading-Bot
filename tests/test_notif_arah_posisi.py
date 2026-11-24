"""Notifikasi harus membedakan posisi LONG (beli) dan SHORT (jual).

Kasus yang jadi pemicu: sinyal SHORT BELI (PT Global Digital Niaga Tbk)
men billed "SAHAM TP3 TERCAPAI" padahal entry 238 dan harga turun ke 198.
Ternyata itu benar - TP3-nya posisi SHORT (harga harus turun), tapi
notifikasinya ditulis dengan konvensi LONG:

- SL short di ATAS entry ditulis "+22.7%" -> terbaca sebagai gain
- target short di BAWAH entry ditulis "-16.7%" -> terbaca sebagai rugi
- tidak ada satupun penanda bahwa ini posisi jual

Akibatnya user mengira TP3-nya salah. Yang dijaga di sini:
1. Arah posisi selalu ditulis (judul + baris posisi).
2. Persentase SL/TP mengikuti arahnya (risiko ke atas, target ke bawah).
3. TP3 menutup posisi penuh, jadi P/L realisasi yang ditampilkan - bukan
   rencana level.

Short/long sebagai SETTING hanya berlaku crypto dulu (lihat
SPEC 3.6). Yang dibedakan di sini adalah tampilannya, untuk saham maupun
crypto - alur sinyal yang berjalan tidak diubah.
"""
import sys
import os
import asyncio
from datetime import datetime, timezone, timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

WIB = timezone(timedelta(hours=7))


def render_stock(notif_type='TP3', entry=238.0, current=198.0,
                 tp1=211.0, tp2=198.0, tp3=198.0, sl=292.0,
                 direction='SHORT', profit_loss=16.8):
    from utils.formatters import format_unified_stock_notification

    return format_unified_stock_notification(
        notif_type=notif_type,
        ticker='BELI',
        name='PT Global Digital Niaga Tbk',
        entry=entry,
        current_price=current,
        tp1=tp1, tp2=tp2, tp3=tp3, sl=sl,
        analysis_data={'indicators': {'atr': entry * 0.02}},
        change_pct=profit_loss,
        profit_loss=profit_loss,
        direction=direction,
    )


class TestShortStockNotification:
    """Posisi SHORT (jual) harus terbaca sebagai jual."""

    def test_tp3_header_states_short(self):
        msg = render_stock(notif_type='TP3')
        assert 'SHORT/JUAL' in msg

    def test_tp3_states_position_direction(self):
        msg = render_stock(notif_type='TP3')
        assert 'Posisi SHORT' in msg
        assert 'harga TURUN' in msg

    def test_tp3_shows_realized_pl(self):
        """TP3 menutup posisi, jadi yang ditampilkan P/L realisasi."""
        msg = render_stock(notif_type='TP3')
        assert 'P/L' in msg
        assert '+16.80%' in msg

    def test_tp3_has_no_long_convention_plan_line(self):
        """Rencana '+22.7% → -16.7%' adalah konvensi LONG - untuk short
        itu tampilannya menipu (risk dibaca gain, target dibaca rugi)."""
        msg = render_stock(notif_type='TP3')
        assert '+22.7% → -16.7%' not in msg
        assert '→ -16.7%' not in msg

    def test_sl_shown_as_risk_not_gain(self):
        """SL short di atas entry - percentasenya risiko, bukan gain."""
        msg = render_stock(notif_type='TP3')
        assert 'risiko 22.7%' in msg

    def test_sell_signal_shows_targets_below_entry(self):
        msg = render_stock(notif_type='SELL', current=238.0, profit_loss=0.0)
        assert 'REKOMENDASI JUAL' in msg
        assert 'TP1' in msg and 'TP2' in msg and 'TP3' in msg
        assert 'target 11.3%' in msg

    def test_sell_signal_gain_line_states_direction(self):
        msg = render_stock(notif_type='SELL', current=238.0, profit_loss=0.0)
        assert 'risiko' in msg
        assert 'harga naik' in msg
        assert 'target' in msg
        assert 'harga turun' in msg


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


class TestShortStockTp3Loop:
    """Sinyal SHORT yang kena TP3 harusClosing dengan untung."""

    def test_short_tp3_notification_is_directional(self, stock_loop):
        from handlers.scheduler import jobs_stocks

        stock_loop['signals'] = {'STOCK_BELI_1': make_short_stock_signal()}
        app = FakeApp()
        asyncio.run(jobs_stocks.check_stock_tp_sl(app))

        # Harga short yang jatuh menembus semua level sekaligus, jadi
        # TP1/TP2/TP3 semua kena dalam satu siklus (ladder 1/3 - 1/3 - 1/3).
        msgs = [text for _, text in app.bot.sent]
        tp3 = [m for m in msgs if 'TP3 TERCAPAI' in m]
        assert tp3, 'notifikasi TP3 tidak terkirim'
        msg = tp3[0]
        assert 'SHORT/JUAL' in msg
        assert 'Posisi SHORT' in msg
        assert 'risiko 3.0%' in msg
        # Harga turun dari 100 ke 93 = +7% buat posisi short.
        assert '+7.00%' in msg
        assert '-7.0%' not in msg

    def test_short_tp3_marks_outcome(self, stock_loop):
        from handlers.scheduler import jobs_stocks

        stock_loop['signals'] = {'STOCK_BELI_1': make_short_stock_signal()}
        app = FakeApp()
        asyncio.run(jobs_stocks.check_stock_tp_sl(app))

        # Harga short yang jatuh menembus semua level sekaligus, jadi
        # TP1/TP2/TP3 semua kena dalam satu siklus (ladder 1/3 - 1/3 - 1/3).
        assert stock_loop['outcomes'] == [
            ('STOCK_BELI_1', 'tp1'),
            ('STOCK_BELI_1', 'tp2'),
            ('STOCK_BELI_1', 'tp3'),
        ]

    def test_short_sl_hit_reports_loss(self, stock_loop):
        """Harga naik melewati SL short -> rugi, ditulis sebagai rugi."""
        from handlers.scheduler import jobs_stocks

        stock_loop['price'] = 105.0
        stock_loop['signals'] = {'STOCK_BELI_1': make_short_stock_signal()}
        app = FakeApp()
        asyncio.run(jobs_stocks.check_stock_tp_sl(app))

        assert len(app.bot.sent) == 1
        msg = app.bot.sent[0][1]
        assert 'SHORT/JUAL' in msg
        assert '-5.00%' in msg
        assert stock_loop['outcomes'] == [('STOCK_BELI_1', 'sl')]
