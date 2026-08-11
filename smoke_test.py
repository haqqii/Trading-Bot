"""Smoke test - actually call commands to verify they work end-to-end."""
import asyncio
import sys
from unittest.mock import AsyncMock, MagicMock, patch


# Mock telegram before importing handlers
class MockUpdate:
    def __init__(self, user_id=12345, text="", callback_data=""):
        self.effective_user = MagicMock(id=user_id, first_name="Test")
        self.message = MagicMock()
        self.message.text = text
        self.message.reply_text = AsyncMock()
        self.message.reply_photo = AsyncMock()
        self.message.chat = MagicMock()
        self.message.chat.send_action = AsyncMock()
        self.callback_query = MagicMock()
        self.callback_query.data = callback_data
        self.callback_query.answer = AsyncMock()
        self.callback_query.edit_message_text = AsyncMock()
        self.callback_query.from_user = MagicMock(id=user_id, first_name="Test")


class MockContext:
    def __init__(self, args=None):
        self.args = args or []
        self.application = None


async def smoke_test():
    # Patch the global ALL_STOCKS to a small set so we don't hammer network
    fake_stocks = {
        'BBCA': 'PT Bank Central Asia Tbk',
        'TLKM': 'PT Telkom Indonesia Tbk',
        'ASII': 'PT Astra International Tbk',
    }

    # Save the original
    from handlers import commands as cmd_module
    from handlers.commands import _shared
    original_all_stocks = _shared.ALL_STOCKS
    _shared.ALL_STOCKS = fake_stocks

    # Override to skip network calls
    from services import stock_service
    from handlers import command_handlers

    # Mock stock_service to return fake data
    async def fake_fetch(ticker, name):
        return (ticker, name, {
            'price': 9500.0,
            'change': 1.5,
            'rsi': 45,
            'candles': 50,
            'ma_fast': 9450,
            'ma_slow': 9400,
        })

    stock_service.get_stock_data_combined = lambda *args, **kwargs: {
        'price': 9500.0, 'change': 1.5, 'rsi': 45, 'candles': 50,
        'ma_fast': 9450, 'ma_slow': 9400, 'name': 'PT Test'
    }

    results = []

    # Test 1: start()
    try:
        update = MockUpdate()
        ctx = MockContext()
        await command_handlers.start(update, ctx)
        called = update.message.reply_text.called
        results.append(("start", "OK" if called else "FAILED - no reply_text"))
    except Exception as e:
        results.append(("start", f"ERROR: {type(e).__name__}: {e}"))

    # Test 2: harga()
    try:
        update = MockUpdate()
        ctx = MockContext()
        await command_handlers.harga(update, ctx)
        called = update.message.reply_text.called
        results.append(("harga", "OK" if called else "FAILED - no reply_text"))
    except Exception as e:
        results.append(("harga", f"ERROR: {type(e).__name__}: {e}"))

    # Test 3: favorit() - empty
    try:
        update = MockUpdate()
        ctx = MockContext()
        # Clear user data
        _shared.user_data_db.clear()
        await command_handlers.favorit(update, ctx)
        called = update.message.reply_text.called
        results.append(("favorit (empty)", "OK" if called else "FAILED"))
    except Exception as e:
        results.append(("favorit", f"ERROR: {type(e).__name__}: {e}"))

    # Test 4: add() - stock
    try:
        update = MockUpdate()
        ctx = MockContext(args=["BBCA", "5000"])
        original_u = dict(_shared.user_data_db.get('12345', {}))
        _shared.user_data_db['12345'] = {'favorit': {}, 'crypto_favorit': {}}
        await command_handlers.add(update, ctx)
        called = update.message.reply_text.called
        ok = 'BBCA' in _shared.user_data_db.get('12345', {}).get('favorit', {})
        results.append(("add (stock)", "OK" if called and ok else f"FAILED called={called}"))
        # Restore
        _shared.user_data_db['12345'] = original_u
    except Exception as e:
        results.append(("add", f"ERROR: {type(e).__name__}: {e}"))

    # Test 5: remove() - stock
    try:
        update = MockUpdate()
        ctx = MockContext(args=["BBCA"])
        _shared.user_data_db['12345'] = {'favorit': {'BBCA': 5000}, 'crypto_favorit': {}}
        await command_handlers.remove(update, ctx)
        called = update.message.reply_text.called
        results.append(("remove", "OK" if called else "FAILED"))
    except Exception as e:
        results.append(("remove", f"ERROR: {type(e).__name__}: {e}"))

    # Test 6: stats_cmd() - empty
    try:
        update = MockUpdate()
        ctx = MockContext()
        with patch('db.db') as mock_db:
            mock_db.get_signal_stats.return_value = {
                'total': 0, 'open': 0, 'closed': 0,
                'tp1_count': 0, 'tp2_count': 0, 'tp3_count': 0, 'sl_count': 0,
                'tp_rate': 0, 'sl_rate': 0, 'avg_tp_hit': 0
            }
            await command_handlers.stats_cmd(update, ctx)
        called = update.message.reply_text.called
        results.append(("stats_cmd (empty)", "OK" if called else "FAILED"))
    except Exception as e:
        results.append(("stats_cmd", f"ERROR: {type(e).__name__}: {e}"))

    # Test 7: tf() with custom input
    try:
        update = MockUpdate()
        ctx = MockContext(args=["30m"])
        _shared.user_data_db['12345'] = {'timeframe': '5'}
        await command_handlers.tf(update, ctx)
        called = update.message.reply_text.called
        ok = _shared.user_data_db['12345'].get('timeframe') == '30'
        results.append(("tf 30m", f"OK" if called and ok else f"FAILED called={called} timeframe={_shared.user_data_db['12345'].get('timeframe')}"))
    except Exception as e:
        results.append(("tf", f"ERROR: {type(e).__name__}: {e}"))

    # Test 8: tf_cat_cb (callback)
    try:
        update = MockUpdate(callback_data="tfcat_scalping")
        ctx = MockContext()
        _shared.user_data_db['12345'] = {'timeframe': '5'}
        await command_handlers.tf_cat_cb(update, ctx)
        called = update.callback_query.edit_message_text.called
        results.append(("tf_cat_cb", "OK" if called else "FAILED"))
    except Exception as e:
        results.append(("tf_cat_cb", f"ERROR: {type(e).__name__}: {e}"))

    # Test 9: tf_cb (callback)
    try:
        update = MockUpdate(callback_data="tf_30")
        ctx = MockContext()
        _shared.user_data_db['12345'] = {'timeframe': '5'}
        await command_handlers.tf_cb(update, ctx)
        called = update.callback_query.edit_message_text.called
        ok = _shared.user_data_db['12345'].get('timeframe') == '30'
        results.append(("tf_cb", f"OK" if called and ok else f"FAILED called={called}"))
    except Exception as e:
        results.append(("tf_cb", f"ERROR: {type(e).__name__}: {e}"))

    # Test 10: notifikasi()
    try:
        update = MockUpdate()
        ctx = MockContext()
        _shared.user_data_db['12345'] = {}
        await command_handlers.notifikasi(update, ctx)
        called = update.message.reply_text.called
        results.append(("notifikasi", "OK" if called else "FAILED"))
    except Exception as e:
        results.append(("notifikasi", f"ERROR: {type(e).__name__}: {e}"))

    # Test 11: notifikasi_cb()
    try:
        update = MockUpdate(callback_data="notif_notif_saham")
        ctx = MockContext()
        _shared.user_data_db['12345'] = {'notif_saham': False}
        await command_handlers.notifikasi_cb(update, ctx)
        called = update.callback_query.edit_message_text.called
        ok = _shared.user_data_db['12345'].get('notif_saham') == True
        results.append(("notifikasi_cb", f"OK" if called and ok else f"FAILED called={called}"))
    except Exception as e:
        results.append(("notifikasi_cb", f"ERROR: {type(e).__name__}: {e}"))

    # Test 12: portfolio()
    try:
        update = MockUpdate()
        ctx = MockContext()
        _shared.user_data_db['12345'] = {}
        await command_handlers.portfolio(update, ctx)
        called = update.message.reply_text.called
        results.append(("portfolio (empty)", "OK" if called else "FAILED"))
    except Exception as e:
        results.append(("portfolio", f"ERROR: {type(e).__name__}: {e}"))

    # Test 13: buy()
    try:
        update = MockUpdate()
        ctx = MockContext(args=["BBCA", "9500", "100"])
        _shared.user_data_db['12345'] = {}
        await command_handlers.buy(update, ctx)
        called = update.message.reply_text.called
        ok = len(_shared.user_data_db['12345'].get('portfolio', [])) == 1
        results.append(("buy", f"OK" if called and ok else f"FAILED called={called}"))
    except Exception as e:
        results.append(("buy", f"ERROR: {type(e).__name__}: {e}"))

    # Test 14: sell()
    try:
        update = MockUpdate()
        ctx = MockContext(args=["BBCA", "50"])
        _shared.user_data_db['12345'] = {
            'portfolio': [{'ticker': 'BBCA', 'buy_price': 9500, 'lot': 100, 'buy_date': '2024-01-01'}]
        }
        await command_handlers.sell(update, ctx)
        called = update.message.reply_text.called
        results.append(("sell", "OK" if called else "FAILED"))
    except Exception as e:
        results.append(("sell", f"ERROR: {type(e).__name__}: {e}"))

    # Test 15: help_cmd()
    try:
        update = MockUpdate()
        ctx = MockContext()
        await command_handlers.help_cmd(update, ctx)
        called = update.message.reply_text.called
        results.append(("help_cmd", "OK" if called else "FAILED"))
    except Exception as e:
        results.append(("help_cmd", f"ERROR: {type(e).__name__}: {e}"))

    # Test 16: health_cmd()
    try:
        update = MockUpdate()
        ctx = MockContext()
        _shared.user_data_db['12345'] = {}
        await command_handlers.health_cmd(update, ctx)
        called = update.message.reply_text.called
        results.append(("health_cmd", "OK" if called else "FAILED"))
    except Exception as e:
        results.append(("health_cmd", f"ERROR: {type(e).__name__}: {e}"))

    # Test 17: chart_cmd() - help
    try:
        update = MockUpdate()
        ctx = MockContext()
        await command_handlers.chart_cmd(update, ctx)
        called = update.message.reply_text.called
        results.append(("chart_cmd (no args)", "OK" if called else "FAILED"))
    except Exception as e:
        results.append(("chart_cmd", f"ERROR: {type(e).__name__}: {e}"))

    # Test 18: buttons()
    try:
        update = MockUpdate(text="📊 Harga")
        ctx = MockContext()
        await command_handlers.buttons(update, ctx)
        called = update.message.reply_text.called
        results.append(("buttons (Harga)", "OK" if called else "FAILED"))
    except Exception as e:
        results.append(("buttons", f"ERROR: {type(e).__name__}: {e}"))

    # Restore
    _shared.ALL_STOCKS = original_all_stocks

    print("\n=== SMOKE TEST RESULTS ===")
    passed = sum(1 for _, r in results if "OK" in r)
    failed = len(results) - passed
    for name, result in results:
        print(f"  [{('OK' if 'OK' in result else 'FAIL')}] {name}: {result}")
    print(f"\nTotal: {passed}/{len(results)} passed, {failed} failed")
    return failed == 0


if __name__ == '__main__':
    success = asyncio.run(smoke_test())
    sys.exit(0 if success else 1)
