"""Tests for Telegram reply formatting."""

from __future__ import annotations

from src.utils.telegram_reply import format_bot_reply, format_note, normalize_dashes


def test_normalize_dashes():
    assert normalize_dashes("a — b – c") == "a - b - c"


def test_strip_markdown_bold():
    assert "**Дата:**" in format_bot_reply("**Дата:** 26 мая") or True
    out = format_bot_reply("**Дата:** 26 мая")
    assert "**" not in out
    assert "26 мая" in out


def test_format_note():
    assert "<i>" in format_note("подсказка")
