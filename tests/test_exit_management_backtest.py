"""Harness backtest harus mengukur hal yang sama dengan bot live.

Tiga jebakan yang sempat membuat hasil A/B aturan keluar menyesatkan:

1. Arah SHORT tidak dikenali. Backtest menyimpan arah sebagai
   'BUY'/'SELL', sedangkan exit_manager butuh 'LONG'/'SHORT'. Kalau
   'SELL' diperlakukan sebagai LONG, semua trade short pakai rumus
   breakeven/trailing yang terbalik - SL tidak pernah bergerak dan
   A/B mengukur fitur yang salah.
2. Exit dicatat di level SL, bukan di harga yang benar-benar dilihat
   bot. Backtest jadi selalu mencatat breakeven sebagai 0,00%,
   padahal bot live hampir tidak pernah menutup persis di entry.
3. Win rate dihitung dari jenis exit ('beakeven' bukan win dan bukan
   loss). Trade yang sama jadi 'sl' di satu lengan A/B dan 'beakeven'
   di lengan lain - angkanya tidak bisa dibandingkan.
"""
import pandas as pd

from services.exit_manager import update_sl
from utils.backtest import simulate_exit, simulate_ladder, summarize


def make_df(rows):
    """DataFrame OHLC dari daftar (open, high, low, close)."""
    df = pd.DataFrame(rows, columns=['Open', 'High', 'Low', 'Close'])
    df['Volume'] = 1000
    return df


class TestShortDirection:
    """Sinyal SELL harus memakai aturan short."""

    def test_sell_sl_moves_to_entry_after_tp1(self):
        """'SELL' harus jalan dengan rumus SHORT.

        SL short di atas entry (104). Setelah TP1 kena, SL turun ke
        entry (100). Kalau 'SELL' tidak dikenali, SL dihitung dengan
        rumus LONG -> max(104, 100) = 104, SL diam dan profit tidak
        pernah terkunci.
        """
        assert update_sl(104, 100, 'SELL', {'tp1': True}, 90, 3, tp1=96) == 100

    def test_sell_trailing_locks_below_entry(self):
        """Trailing short = level di bawah entry (profit terkunci)."""
        locked = update_sl(104, 100, 'SELL', {'tp1': True, 'tp2': True},
                           91, 3, tp1=96)
        assert locked < 100

    def test_ladder_short_exits_as_trailing(self):
        """Skenario lengkap: TP1 -> TP2 -> SL trailing pada sinyal SELL.

        Bar:
          1. high 99 low 95 -> TP1 (96) kena, SL turun ke entry (100)
          2. high 99 low 91 -> TP2 (92) kena, SL trailing naik
          3. high 94 low 90 -> belum kena
          4. high 97 low 90 -> SL trailing kena -> exit 'trailing'

        Bar 1-2 high-nya di bawah SL (100) supaya SL belum kena duluan
        - dalam satu bar, SL dicek sebelum TP (asumsi pesimis).

        Dengan bug arah, SL tidak pernah bergerak dan trade berakhir
        timeout (posisi menggantung) - persis masalah yang fitur ini
        mau perbaiki.
        """
        df = make_df([
            (100, 100, 100, 100),
            (99, 99, 95, 96),
            (99, 99, 91, 92),
            (94, 94, 90, 91),
            (97, 97, 90, 91),
        ])

        reason, realized, r_mult, bars = simulate_ladder(
            df, 0, 'SELL', 100.0, 96.0, 92.0, 88.0, 104.0, 3.0, 5,
            manage_exit=True)

        assert reason == 'trailing'
        assert realized > 0
        assert r_mult > 0


class TestRealisticFill:
    """Exit dicatat di harga yang dilihat bot, bukan di level SL."""

    def test_breakeven_exit_not_always_zero(self):
        """Breakeven exit di harga pasar, bukan persis di entry.

        Bar 3 ditutup di 99 - di bawah entry (100). Bot live menutup
        di harga yang dilihat, jadi P/L-nya -1%, bukan 0%.
        """
        df = make_df([
            (100, 100, 100, 100),
            (103, 103, 100, 103),
            (101, 101, 98, 99),
        ])

        reason, exit_price, bars = simulate_exit(
            df, 0, 'BUY', 100.0, 102.0, 104.0, 107.0, 97.0, 1.0, 5,
            manage_exit=True)

        assert reason == 'beakeven'
        assert exit_price == 99.0
        assert exit_price < 100.0

    def test_exit_at_stop_when_bar_closes_better(self):
        """Kalau bar ditutup di sisi baik dari stop, isi di level stop.

        Bot bisa saja menutup tepat di level stop waktu harga menyentuh
        itu - jadi asumsi konservatifnya boleh pakai level stop.
        """
        df = make_df([
            (100, 100, 100, 100),
            (103, 103, 100, 103),
            (102, 102, 96, 101),
        ])

        reason, exit_price, bars = simulate_exit(
            df, 0, 'BUY', 100.0, 102.0, 104.0, 107.0, 97.0, 1.0, 5,
            manage_exit=True)

        assert reason == 'beakeven'
        assert exit_price == 100.0


class TestComparableWinRate:
    """Win rate harus bisa dibandingkan antar lengan A/B."""

    @staticmethod
    def _trades(outcome, ret_pct):
        return [{
            'signal': 'BUY', 'quality': 'STRONG', 'risk_reward': 1.5,
            'outcome': outcome, 'exit_ret_pct': ret_pct, 'r_multiple': 1.0,
        }]

    def test_same_trade_classified_same_in_both_arms(self):
        """Trade yang sama harus masuk hitungan yang sama di dua lengan."""
        off = (self._trades('sl', -3.0) + self._trades('tp3', 6.0))
        on = (self._trades('beakeven', -0.5) + self._trades('trailing', 1.5))

        t_off = summarize(off, 0.16)
        t_on = summarize(on, 0.16)

        assert t_off.iloc[-1]['win_rate_decided'] == '50%'
        assert t_on.iloc[-1]['win_rate_decided'] == '50%'

    def test_trailing_closed_at_loss_is_not_a_win(self):
        """Trailing yang menutup di bawah entry = loss, bukan win."""
        table = summarize(self._trades('trailing', -1.5), 0.16)
        assert table.iloc[-1]['win_rate_decided'] == '0%'

    def test_beakeven_closed_at_loss_is_not_a_win(self):
        """Breakeven yang menutup di bawah entry = loss, bukan win."""
        table = summarize(self._trades('beakeven', -0.5), 0.16)
        assert table.iloc[-1]['win_rate_decided'] == '0%'

    def test_timeout_not_counted_as_decided(self):
        """Trade yang masih hidup saat horizon bukan trade yang diputuskan."""
        trades = self._trades('tp3', 6.0) + [{
            'signal': 'BUY', 'quality': 'STRONG', 'risk_reward': 1.5,
            'outcome': 'timeout', 'exit_ret_pct': 0.5, 'r_multiple': 0.1,
        }]
        table = summarize(trades, 0.16)

        assert table.iloc[-1]['win_rate_decided'] == '100%'
        assert table.iloc[-1]['timeout'] == '50%'

    def test_percentages_add_up_to_100(self):
        """Win + loss + timeout harus 100%.

        Trade timeout yang punya hasil P/L positif sebelumnya ikut
        dihitung sebagai win, jadi kolomnya melebihi 100%.
        """
        trades = (self._trades('tp3', 6.0) * 2 + self._trades('sl', -3.0) + [{
            'signal': 'BUY', 'quality': 'STRONG', 'risk_reward': 1.5,
            'outcome': 'timeout', 'exit_ret_pct': 0.5, 'r_multiple': 0.1,
        }])
        row = summarize(trades, 0.16).iloc[-1]

        win = int(str(row['win']).rstrip('%'))
        loss = int(str(row['loss']).rstrip('%'))
        timeout = int(str(row['timeout']).rstrip('%'))
        assert win + loss + timeout == 100
