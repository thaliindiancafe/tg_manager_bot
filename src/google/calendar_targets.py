"""Resolve primary + events Google Calendar IDs from settings."""

from __future__ import annotations

from src.config import settings


def primary_calendar_id() -> str:
    """Main restaurant calendar (read)."""
    for candidate in (
        settings.calendar_primary_id,
        settings.calendar_id,
    ):
        value = (candidate or "").strip()
        if value:
            return value
    return "primary"


def events_calendar_id() -> str:
    """Calendar for bot-created events (write). Falls back to primary if unset."""
    value = (settings.calendar_events_id or "").strip()
    if value:
        return value
    return primary_calendar_id()


def calendars_for_read() -> list[tuple[str, str]]:
    """
    Unique (calendar_id, human label) for today's events query.
    Includes primary and events calendars when both configured.
    """
    primary_id = primary_calendar_id()
    events_id = (settings.calendar_events_id or "").strip()

    primary_label = (settings.calendar_primary_label or "Основной").strip()
    events_label = (settings.calendar_events_label or "Мероприятия").strip()

    out: list[tuple[str, str]] = []
    seen: set[str] = set()

    for cid, label in ((primary_id, primary_label), (events_id, events_label)):
        if not cid or cid in seen:
            continue
        seen.add(cid)
        out.append((cid, label))

    if not out:
        out.append(("primary", "Основной"))
    return out
