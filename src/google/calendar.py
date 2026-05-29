"""Google Calendar access via service account (async wrappers)."""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from google.oauth2 import service_account
from googleapiclient.discovery import Resource, build

from src.config import settings
from src.google.oauth_credentials import (
    USER_OAUTH_SCOPES,
    load_user_credentials,
    oauth_configured,
    oauth_has_calendar_read_scope,
)

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/calendar"]
DATE_FORMAT = "%Y-%m-%d"
TIME_FORMAT = "%H:%M"
DEFAULT_EVENT_DURATION = timedelta(hours=1)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_read_service: Resource | None = None
_read_service_mode: str | None = None
_write_service: Resource | None = None


def _resolve_credentials_path() -> Path:
    path = Path(settings.google_credentials_json)
    if not path.is_absolute():
        path = _PROJECT_ROOT / path
    if not path.is_file():
        raise FileNotFoundError(f"Service account file not found: {path}")
    return path


def _get_timezone() -> ZoneInfo:
    try:
        return ZoneInfo(settings.timezone)
    except Exception as exc:
        logger.error("Invalid TIMEZONE=%s: %s", settings.timezone, exc)
        raise ValueError(
            f"Invalid TIMEZONE '{settings.timezone}'. Use IANA name, e.g. Europe/Moscow."
        ) from exc


def _build_service_account() -> Resource:
    credentials = service_account.Credentials.from_service_account_file(
        str(_resolve_credentials_path()),
        scopes=SCOPES,
    )
    return build("calendar", "v3", credentials=credentials, cache_discovery=False)


def _build_read_service() -> Resource:
    """Read events: OAuth (client Gmail) when configured, else service account."""
    global _read_service, _read_service_mode

    use_oauth = (
        settings.google_tasks_use_oauth
        and oauth_configured()
        and oauth_has_calendar_read_scope()
    )
    if (
        settings.google_tasks_use_oauth
        and oauth_configured()
        and not oauth_has_calendar_read_scope()
    ):
        logger.warning(
            "OAuth token has no Calendar read scope (403 on events). "
            "Re-run: python scripts/google_tasks_oauth_setup.py — "
            "using service account for Calendar read until then."
        )
    mode = "oauth" if use_oauth else "service_account"

    if _read_service is not None and _read_service_mode == mode:
        return _read_service

    if use_oauth:
        credentials = load_user_credentials(USER_OAUTH_SCOPES)
        logger.debug("Google Calendar read: OAuth user credentials")
    else:
        credentials = service_account.Credentials.from_service_account_file(
            str(_resolve_credentials_path()),
            scopes=SCOPES,
        )
        logger.debug("Google Calendar read: service account")

    _read_service = build(
        "calendar", "v3", credentials=credentials, cache_discovery=False
    )
    _read_service_mode = mode
    return _read_service


def _build_write_service() -> Resource:
    """Create/delete events via service account (календарь «Мероприятия»)."""
    global _write_service
    if _write_service is not None:
        return _write_service
    _write_service = _build_service_account()
    return _write_service


def _parse_date(date_str: str) -> date:
    try:
        return datetime.strptime(date_str.strip(), DATE_FORMAT).date()
    except ValueError as exc:
        raise ValueError(f"date must be YYYY-MM-DD, got: {date_str!r}") from exc


def _parse_time(time_str: str) -> tuple[int, int]:
    try:
        parsed = datetime.strptime(time_str.strip(), TIME_FORMAT)
        return parsed.hour, parsed.minute
    except ValueError as exc:
        raise ValueError(f"time must be HH:MM, got: {time_str!r}") from exc


def _combine_datetime(date_str: str, time_str: str) -> datetime:
    event_date = _parse_date(date_str)
    hour, minute = _parse_time(time_str)
    tz = _get_timezone()
    return datetime(
        event_date.year,
        event_date.month,
        event_date.day,
        hour,
        minute,
        tzinfo=tz,
    )


def _extract_event_datetime(
    payload: dict[str, Any], field: str
) -> tuple[str | None, str | None]:
    """Return (date YYYY-MM-DD, time HH:MM) from start/end block."""
    block = payload.get(field, {})
    if "dateTime" in block:
        dt = datetime.fromisoformat(block["dateTime"].replace("Z", "+00:00"))
        local = dt.astimezone(_get_timezone())
        return local.strftime(DATE_FORMAT), local.strftime(TIME_FORMAT)
    if "date" in block:
        return block["date"], None
    return None, None


def _event_to_dict(
    event: dict[str, Any],
    *,
    source_calendar_id: str = "",
    source_calendar_label: str = "",
) -> dict[str, Any]:
    event_date, event_time = _extract_event_datetime(event, "start")
    end_date, end_time = _extract_event_datetime(event, "end")
    return {
        "id": event.get("id", ""),
        "title": event.get("summary", ""),
        "date": event_date,
        "time": event_time,
        "end_date": end_date,
        "end_time": end_time,
        "description": event.get("description", ""),
        "html_link": event.get("htmlLink", ""),
        "status": event.get("status", ""),
        "source_calendar_id": source_calendar_id,
        "source_calendar_label": source_calendar_label,
    }


def _get_events_for_day_sync(
    calendar_id: str,
    day: date,
    *,
    source_calendar_label: str = "",
) -> list[dict[str, Any]]:
    tz = _get_timezone()
    day_start = datetime(day.year, day.month, day.day, tzinfo=tz)
    day_end = day_start + timedelta(days=1)

    service = _build_read_service()
    result = (
        service.events()
        .list(
            calendarId=calendar_id,
            timeMin=day_start.isoformat(),
            timeMax=day_end.isoformat(),
            singleEvents=True,
            orderBy="startTime",
        )
        .execute()
    )
    label = source_calendar_label or calendar_id
    return [
        _event_to_dict(
            item,
            source_calendar_id=calendar_id,
            source_calendar_label=label,
        )
        for item in result.get("items", [])
    ]


def _list_events_in_range_sync(
    calendar_id: str,
    range_start: date,
    range_end: date,
    *,
    source_calendar_label: str = "",
) -> list[dict[str, Any]]:
    """All non-cancelled instances in [range_start, range_end] inclusive."""
    if range_end < range_start:
        return []

    tz = _get_timezone()
    start_dt = datetime(
        range_start.year,
        range_start.month,
        range_start.day,
        tzinfo=tz,
    )
    end_dt = datetime(
        range_end.year,
        range_end.month,
        range_end.day,
        tzinfo=tz,
    ) + timedelta(days=1)

    service = _build_read_service()
    result = (
        service.events()
        .list(
            calendarId=calendar_id,
            timeMin=start_dt.isoformat(),
            timeMax=end_dt.isoformat(),
            singleEvents=True,
            orderBy="startTime",
        )
        .execute()
    )
    label = source_calendar_label or calendar_id
    out: list[dict[str, Any]] = []
    for item in result.get("items", []):
        if str(item.get("status", "")).strip() == "cancelled":
            continue
        out.append(
            _event_to_dict(
                item,
                source_calendar_id=calendar_id,
                source_calendar_label=label,
            )
        )
    return out


async def list_events_in_range(
    calendar_id: str,
    start_date: str,
    end_date: str,
    *,
    source_calendar_label: str = "",
) -> list[dict[str, Any]]:
    """List events between YYYY-MM-DD dates (inclusive)."""
    try:
        start = _parse_date(start_date)
        end = _parse_date(end_date)
        return await asyncio.to_thread(
            _list_events_in_range_sync,
            calendar_id,
            start,
            end,
            source_calendar_label=source_calendar_label,
        )
    except Exception as exc:
        logger.error(
            "list_events_in_range failed: calendar_id=%s start=%s end=%s error=%s",
            calendar_id,
            start_date,
            end_date,
            exc,
            exc_info=True,
        )
        raise


def _get_today_events_sync(
    calendar_id: str,
    *,
    source_calendar_label: str = "",
) -> list[dict[str, Any]]:
    tz = _get_timezone()
    today = datetime.now(tz).date()
    return _get_events_for_day_sync(
        calendar_id,
        today,
        source_calendar_label=source_calendar_label,
    )


def _sort_events_by_time(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    def _key(row: dict[str, Any]) -> tuple[str, str]:
        return (
            str(row.get("date") or ""),
            str(row.get("time") or "99:99"),
        )

    return sorted(events, key=_key)


def _create_event_sync(
    calendar_id: str,
    title: str,
    date_str: str,
    time_str: str,
    description: str,
) -> str:
    start_dt = _combine_datetime(date_str, time_str)
    end_dt = start_dt + DEFAULT_EVENT_DURATION
    tz_name = settings.timezone

    body = {
        "summary": title,
        "description": description,
        "start": {"dateTime": start_dt.isoformat(), "timeZone": tz_name},
        "end": {"dateTime": end_dt.isoformat(), "timeZone": tz_name},
    }

    service = _build_write_service()
    created = (
        service.events()
        .insert(calendarId=calendar_id, body=body)
        .execute()
    )
    event_id = created.get("id")
    if not event_id:
        raise RuntimeError("Google Calendar did not return event id")
    html_link = str(created.get("htmlLink", "") or "").strip()
    return {"event_id": str(event_id), "html_link": html_link}


def _delete_event_sync(calendar_id: str, event_id: str) -> None:
    service = _build_write_service()
    service.events().delete(calendarId=calendar_id, eventId=event_id).execute()


async def get_today_events(
    calendar_id: str,
    *,
    source_calendar_label: str = "",
) -> list[dict[str, Any]]:
    """Return today's events for one calendar in configured timezone."""
    try:
        return await asyncio.to_thread(
            _get_today_events_sync,
            calendar_id,
            source_calendar_label=source_calendar_label,
        )
    except Exception as exc:
        logger.error(
            "get_today_events failed: calendar_id=%s error=%s",
            calendar_id,
            exc,
            exc_info=True,
        )
        raise


async def get_events_for_day(
    calendar_id: str,
    date_str: str,
    *,
    source_calendar_label: str = "",
) -> list[dict[str, Any]]:
    """Return calendar events for one calendar on a given YYYY-MM-DD day."""
    try:
        day = _parse_date(date_str)
        return await asyncio.to_thread(
            _get_events_for_day_sync,
            calendar_id,
            day,
            source_calendar_label=source_calendar_label,
        )
    except Exception as exc:
        logger.error(
            "get_events_for_day failed: calendar_id=%s date=%s error=%s",
            calendar_id,
            date_str,
            exc,
            exc_info=True,
        )
        raise


async def get_today_events_from_calendars(
    calendars: list[tuple[str, str]],
) -> list[dict[str, Any]]:
    """Merge today's events from several calendars, sorted by time."""
    tz = _get_timezone()
    today = datetime.now(tz).strftime(DATE_FORMAT)
    return await get_events_for_dates_from_calendars(calendars, [today])


async def get_events_for_dates_from_calendars(
    calendars: list[tuple[str, str]],
    dates: list[str],
) -> list[dict[str, Any]]:
    """Merge events from several calendars for the given YYYY-MM-DD dates."""
    merged: list[dict[str, Any]] = []
    unique_dates = sorted({d.strip()[:10] for d in dates if d.strip()})
    for calendar_id, label in calendars:
        for date_str in unique_dates:
            try:
                rows = await get_events_for_day(
                    calendar_id,
                    date_str,
                    source_calendar_label=label,
                )
                merged.extend(rows)
            except Exception as exc:
                logger.warning(
                    "get_events_for_dates_from_calendars skipped "
                    "calendar_id=%s label=%s date=%s error=%s",
                    calendar_id,
                    label,
                    date_str,
                    exc,
                    exc_info=True,
                )
    return _sort_events_by_time(merged)


async def create_event(
    calendar_id: str,
    title: str,
    date: str,
    time: str,
    description: str,
) -> dict[str, str]:
    """Create a calendar event. date=YYYY-MM-DD, time=HH:MM. Returns event_id and html_link."""
    try:
        return await asyncio.to_thread(
            _create_event_sync,
            calendar_id,
            title,
            date,
            time,
            description,
        )
    except Exception as exc:
        logger.error(
            "create_event failed: calendar_id=%s title=%s date=%s time=%s error=%s",
            calendar_id,
            title,
            date,
            time,
            exc,
            exc_info=True,
        )
        raise


async def delete_event(calendar_id: str, event_id: str) -> None:
    """Delete a calendar event by id."""
    try:
        await asyncio.to_thread(_delete_event_sync, calendar_id, event_id)
    except Exception as exc:
        logger.error(
            "delete_event failed: calendar_id=%s event_id=%s error=%s",
            calendar_id,
            event_id,
            exc,
            exc_info=True,
        )
        raise
