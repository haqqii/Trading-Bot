"""Timeframe menu - /tf command and category/sub-category callbacks."""
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from utils.formatters import TIMEFRAMES
from config.settings import INTERVAL_TO_KEY, VALID_INTERVALS
from ._shared import get_user, save_user_data, _safe_query_answer

logger = logging.getLogger(__name__)

TIMEFRAME_DESCRIPTIONS = {
    '1':    ('1 Menit',  'Scalping - trading sangat cepat (hold 1-5 menit). Untuk trader berpengalaman.'),
    '5':    ('5 Menit',  'Default. Cocok untuk pemula & trader harian (hold 15-60 menit).'),
    '15':   ('15 Menit', 'Intraday swing. Hold 1-4 jam, tren lebih jelas terlihat.'),
    '30':   ('30 Menit', 'Swing pendek. Hold 1-2 jam, noise lebih sedikit.'),
    '60':   ('1 Jam',    'Swing trading. Hold 1-3 hari, sinyal lebih akurat.'),
    '240':  ('4 Jam',    'Swing jangka panjang. Hold beberapa hari, tren utama.'),
    '1440': ('1 Hari',   'Position trading. Hold 1-4 minggu, analisa jangka panjang.'),
}

TF_CATEGORIES = {
    'scalping': {
        'name': 'Scalping',
        'desc': 'Trading sangat cepat. Hold 1-5 menit. Untuk trader berpengalaman.',
        'emoji': '⚡',
        'timeframes': ['1', '5'],
    },
    'daytrade': {
        'name': 'Daytrade',
        'desc': 'Trading harian. Hold 15 menit - 1 hari. Cocok untuk pemula.',
        'emoji': '🎯',
        'timeframes': ['15', '30'],
    },
    'swing': {
        'name': 'Swing',
        'desc': 'Swing trading. Hold 1-3 hari. Sinyal lebih akurat.',
        'emoji': '📈',
        'timeframes': ['60', '240'],
    },
    'long_trade': {
        'name': 'Long Trade',
        'desc': 'Position trading. Hold 1-4 minggu. Untuk analisa jangka panjang.',
        'emoji': '🏔️',
        'timeframes': ['1440'],
    },
}


def _get_category_for_key(tf_key: str) -> str:
    """Get the category name for a given timeframe key."""
    for cat_key, cat in TF_CATEGORIES.items():
        if tf_key in cat['timeframes']:
            return cat_key
    return 'daytrade'


async def tf(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    u = get_user(uid)
    curr = u.get('timeframe', '5')

    if ctx.args:
        raw = ctx.args[0].lower().strip()
        tf_key = INTERVAL_TO_KEY.get(raw)
        if tf_key:
            u['timeframe'] = tf_key
            save_user_data()
            name = TIMEFRAMES[tf_key]['name']
            _, desc = TIMEFRAME_DESCRIPTIONS.get(tf_key, (name, ''))
            await update.message.reply_text(
                f"✅ Timeframe diubah ke: *{name}*\n\n_{desc}_",
                parse_mode='Markdown'
            )
            return
        else:
            valid = ', '.join(sorted(VALID_INTERVALS))
            await update.message.reply_text(
                f"⚠️ Interval *{raw}* tidak dikenali.\n\n"
                f"Valid: `{valid}`\n\n"
                "Contoh: `/tf 30m` atau `/tf 4h`",
                parse_mode='Markdown'
            )
            return

    curr_cat = _get_category_for_key(curr)
    curr_cat_name = TF_CATEGORIES[curr_cat]['name']

    kb = []
    for cat_key, cat in TF_CATEGORIES.items():
        marker = '✅ ' if cat_key == curr_cat else '⚪ '
        kb.append([InlineKeyboardButton(
            f"{marker}{cat['emoji']} {cat['name']}",
            callback_data=f"tfcat_{cat_key}"
        )])

    msg = f"⏱️ *PILIH KATEGORI TIMEFRAME*\n\n"
    msg += f"_Timeframe saat ini: *{TIMEFRAMES[curr]['name']}* ({curr_cat_name})_\n\n"
    msg += "_Pilih gaya trading-mu:_\n\n"
    for cat_key, cat in TF_CATEGORIES.items():
        marker = '✅' if cat_key == curr_cat else '⚪'
        msg += f"{marker} {cat['emoji']} *{cat['name']}* - {cat['desc']}\n"

    msg += "\n_Atau ketik: /tf 30m, /tf 4h, /tf 1d_"

    await update.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')


async def tf_cat_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Show timeframe options within a category."""
    query = update.callback_query
    await _safe_query_answer(query)
    cat_key = query.data.replace('tfcat_', '')
    uid = str(query.from_user.id)
    u = get_user(uid)
    curr = u.get('timeframe', '5')

    if cat_key == 'back':
        await query.edit_message_text("⚠️ Sudah dihapus.")
        return

    cat = TF_CATEGORIES.get(cat_key)
    if not cat:
        await query.edit_message_text("⚠️ Kategori tidak dikenali.")
        return

    kb = []
    for tf_key in cat['timeframes']:
        v = TIMEFRAMES[tf_key]
        _, desc = TIMEFRAME_DESCRIPTIONS.get(tf_key, (v['name'], ''))
        kb.append([InlineKeyboardButton(
            f"{'✅ ' if tf_key == curr else '⚪ '}{v['name']} - {desc[:35]}{'...' if len(desc) > 35 else ''}",
            callback_data=f"tf_{tf_key}"
        )])

    msg = f"{cat['emoji']} *{cat['name'].upper()}*\n\n"
    msg += f"_{cat['desc']}_\n\n"
    msg += "_Pilih timeframe untuk analisa:_\n\n"
    for tf_key in cat['timeframes']:
        v = TIMEFRAMES[tf_key]
        name, desc = TIMEFRAME_DESCRIPTIONS.get(tf_key, (v['name'], ''))
        marker = '✅' if tf_key == curr else '⚪'
        msg += f"{marker} *{name}* - {desc}\n"

    await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')


async def tf_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await _safe_query_answer(query)
    tf_key = query.data.replace('tf_', '')
    uid = str(query.from_user.id)
    get_user(uid)['timeframe'] = tf_key
    save_user_data()
    name, desc = TIMEFRAME_DESCRIPTIONS.get(tf_key, (TIMEFRAMES[tf_key]['name'], ''))
    cat_key = _get_category_for_key(tf_key)
    cat_name = TF_CATEGORIES[cat_key]['name']
    await query.edit_message_text(
        f"✅ Timeframe diubah ke: *{name}*\n\n"
        f"📂 Kategori: {cat_name}\n\n"
        f"_{desc}_",
        parse_mode='Markdown'
    )
