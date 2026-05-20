"""Tests for TASK_REMINDER_HOURS scheduling."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from src.scheduler.reminder_schedule import (
    parse_reminder_hours_csv,
    should_run_task_reminders_now,
)


def test_parse_hours():
    assert parse_reminder_hours_csv("") is None
    assert parse_reminder_hours_csv("10,18") == [10, 18]


def test_run_at_configured_hour():
    tz = ZoneInfo("Europe/Moscow")
    now = datetime(2026, 5, 20, 10, 1, tzinfo=tz)
    assert should_run_task_reminders_now(now, [10, 18], window_minutes=3) is True
    now_skip = datetime(2026, 5, 20, 12, 0, tzinfo=tz)
    assert should_run_task_reminders_now(now_skip, [10, 18], window_minutes=3) is False


def test_run_every_tick_when_hours_empty():
    tz = ZoneInfo("Europe/Moscow")
    now = datetime(2026, 5, 20, 3, 30, tzinfo=tz)
    assert should_run_task_reminders_now(now, None, window_minutes=3) is True
