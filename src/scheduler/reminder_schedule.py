"""When to run task due reminders (not every scheduler tick)."""

from __future__ import annotations

from datetime import datetime, timedelta


def parse_reminder_hours_csv(raw: str) -> list[int] | None:
    """
    Parse TASK_REMINDER_HOURS like «10,18».

    Empty / unset → None (run on every scheduler tick, legacy behaviour).
    """
    text = (raw or "").strip()
    if not text:
        return None

    hours: list[int] = []
    for part in text.split(","):
        piece = part.strip()
        if not piece:
            continue
        try:
            hour = int(piece)
        except ValueError:
            continue
        if 0 <= hour <= 23 and hour not in hours:
            hours.append(hour)
    return sorted(hours) if hours else None


def should_run_task_reminders_now(
    now: datetime,
    hours: list[int] | None,
    *,
    window_minutes: int = 3,
) -> bool:
    """
    True if reminders should run at this moment.

    When hours is None — always True (check each 5 min).
    Otherwise only within ±window_minutes of HH:00 for listed hours.
    """
    if not hours:
        return True

    window = max(1, min(int(window_minutes), 15))
    tolerance = timedelta(minutes=window)

    for hour in hours:
        scheduled = now.replace(hour=hour, minute=0, second=0, microsecond=0)
        if abs(now - scheduled) <= tolerance:
            return True
    return False
