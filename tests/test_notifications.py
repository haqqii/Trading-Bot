"""
Unit tests for handlers/commands/notifications.py
Tests that the toggle reply stays parseable by Telegram legacy Markdown.
"""
import sys
import os

import pytest

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def build_reply(notif_key):
    """Jalankan notifikasi_cb dengan query palsu,return teks yang dikirim."""
    import asyncio
    from handlers.commands import notifications as notif_mod

    captured = {}

    class FakeQuery:
        data = f'notif_{notif_key}'
        from_user = type('U', (), {'id': 12345})()

        async def edit_message_text(self, text, **kwargs):
            captured['text'] = text
            captured['kwargs'] = kwargs

    class FakeUpdate:
        callback_query = FakeQuery()

    notif_mod.get_user = lambda uid: {notif_key: False}
    notif_mod.save_user = lambda uid: None
    notif_mod._schedule_answer = lambda q: None

    asyncio.run(notif_mod.notifikasi_cb(FakeUpdate(), None))
    return captured


def unescaped_underscores(text):
    """Hitung underscore yang tidak di-escape."""
    count = 0
    i = 0
    while i < len(text):
        if text[i] == '\\':
            i += 2
            continue
        if text[i] == '_':
            count += 1
        i += 1
    return count


class TestNotificationToggleMarkdown:
    """Kunci notifikasi mengandung underscore, harus di-escape."""

    def test_key_with_underscore_is_escaped(self):
        """notif_alert_favorit tidak boleh mentah-mentah masuk pesan."""
        out = build_reply('notif_alert_favorit')
        assert 'notif\\_alert\\_favorit' in out['text']
        assert 'notif_alert_favorit' not in out['text']

    def test_italic_markers_balanced(self):
        """Penanda italic harus berpasangan, kalau tidak Telegram tolak."""
        for key in ('notif_saham', 'notif_alert_favorit', 'notif_morning'):
            out = build_reply(key)
            assert unescaped_underscores(out['text']) % 2 == 0, out['text']

    def test_status_shown(self):
        """Status ON/OFF tetap muncul di pesan."""
        out = build_reply('notif_alert_favorit')
        assert 'Update' in out['text']
        assert 'ON' in out['text'] or 'OFF' in out['text']

    def test_parse_mode_markdown(self):
        """Pesan tetap pakai legacy Markdown."""
        out = build_reply('notif_alert_favorit')
        assert out['kwargs'].get('parse_mode') == 'Markdown'
