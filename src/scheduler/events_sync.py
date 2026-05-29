"""Daily sync: Google Calendar → spreadsheet events tab (current month)."""

from __future__ import annotations

import calendar as cal_mod
import logging
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from src.config import settings
from src.google import calendar as google_calendar
from src.google import sheets
from src.google.calendar_targets import calendars_for_read

logger = logging.getLogger(__name__)

EVENTS_SYNC_MEMORY_EMPLOYEE = "_system_calendar"


def _get_tz() -> ZoneInfo:
    return ZoneInfo(settings.timezone)


def _current_month_bounds(now: datetime) -> tuple[date, date]:
    """First and last calendar day of the month in app timezone."""
    y, m = now.year, now.month
    last_day = cal_mod.monthrange(y, m)[1]
    return date(y, m, 1), date(y, m, last_day)


def _calendar_event_to_sheet_row(event: dict[str, Any]) -> dict[str, str] | None:
    event_id = str(event.get("id", "")).strip()
    event_date = str(event.get("date", "")).strip()[:10]
    if not event_id or not event_date:
        return None

    label = str(event.get("source_calendar_label", "")).strip() or "calendar"
    description = str(event.get("description", "")).strip()
    html_link = str(event.get("html_link", "")).strip()
    if html_link and html_link not in description:
        description = f"{description}\n{html_link}".strip() if description else html_link

    return {
        "title": str(event.get("title", "")).strip() or "(без названия)",
        "date": event_date,
        "time": str(event.get("time") or "").strip(),
        "description": description[:8000],
        "calendar_id": event_id,
        "created_by": f"sync:{label}",
    }


def _events_sync_fact_text() -> str:
    return (
        "Календарь Google: create_event пишет в CALENDAR_EVENTS_ID и в лист events. "
        "Лист events обновляется автоматически каждый день (EVENTS_SYNC_HOUR:MINUTE, "
        "по умолчанию 07:05 МСК) из основного календаря + «Мероприятия» за текущий месяц. "
        "На вопросы про встречи — get_events_for_dates; колонка calendar_id = id события Google."
    )


async def sync_events_from_google_calendars() -> int:
    """
    Replace events sheet rows with events from configured read calendars
    for the current calendar month.
    """
    # In calendar-only mode (or DB backend), we do not maintain the Sheets mirror.
    if getattr(settings, "calendar_only_mode", False) or settings.storage_backend.strip().lower() == "db":
        logger.debug("sync_events_from_google_calendars: skipped (calendar_only_mode/db)")
        return 0

    if not settings.events_sync_enabled:
        logger.debug("sync_events_from_google_calendars: disabled")
        return 0

    now = datetime.now(_get_tz())
    range_start, range_end = _current_month_bounds(now)
    start_iso = range_start.isoformat()
    end_iso = range_end.isoformat()

    sheet_rows: list[dict[str, str]] = []
    seen_event_ids: set[str] = set()

    for calendar_id, label in calendars_for_read():
        try:
            raw = await google_calendar.list_events_in_range(
                calendar_id,
                start_iso,
                end_iso,
                source_calendar_label=label,
            )
        except Exception as exc:
            logger.error(
                "sync_events: calendar=%s label=%s error=%s",
                calendar_id,
                label,
                exc,
                exc_info=True,
            )
            continue

        for event in raw:
            row = _calendar_event_to_sheet_row(event)
            if row is None:
                continue
            eid = row["calendar_id"]
            if eid in seen_event_ids:
                continue
            seen_event_ids.add(eid)
            sheet_rows.append(row)

    sheet_rows.sort(key=lambda r: (r.get("date", ""), r.get("time", "")))

    try:
        await sheets.replace_events_rows(sheet_rows)
        try:
            await sheets.upsert_memory_fact_row(
                EVENTS_SYNC_MEMORY_EMPLOYEE,
                _events_sync_fact_text(),
            )
        except Exception as mem_exc:
            logger.warning(
                "sync_events: sheet OK but memory_facts upsert failed: %s",
                mem_exc,
                exc_info=True,
            )
        logger.info(
            "sync_events_from_google_calendars: %s rows month=%s..%s",
            len(sheet_rows),
            start_iso,
            end_iso,
        )
        return len(sheet_rows)
    except Exception as exc:
        logger.error("sync_events_from_google_calendars failed: %s", exc, exc_info=True)
        raise
