"""Direct answers for calendar event queries (встречи, мероприятия)."""

from __future__ import annotations

import logging
import re
from typing import Any

from src.agent import tools as agent_tools
from src.bot.employee_tasks_query import _detect_date_preset
from src.config import settings
from src.google.oauth_credentials import oauth_configured, oauth_has_calendar_read_scope

logger = logging.getLogger(__name__)

_EVENT_WORD = re.compile(
    r"встреч|мероприят|событи|план[её]р|календар",
    re.IGNORECASE,
)
_PLANNED_WORD = re.compile(r"запланирован|на\s+сегодня|на\s+завтра", re.IGNORECASE)
_TASK_WORD = re.compile(r"задач|поручен", re.IGNORECASE)


def _looks_like_calendar_events_query(text: str) -> bool:
    if _TASK_WORD.search(text) and not _EVENT_WORD.search(text):
        return False
    if _EVENT_WORD.search(text):
        return True
    if _PLANNED_WORD.search(text) and not _TASK_WORD.search(text):
        return True
    return False


def _format_google_tasks_section(tasks_data: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    err = tasks_data.get("google_tasks_error")
    if err:
        lines.append(f"Задачи Google Tasks: не загрузились ({err})")
        return lines

    google_rows = tasks_data.get("google_tasks") or []
    if not google_rows:
        return lines

    lines.append("Задачи (Google Tasks, как в календаре Gmail):")
    for row in google_rows:
        title = str(row.get("title", "")).strip() or "(без названия)"
        list_title = str(row.get("tasklist_title", "")).strip()
        hint = f" — {list_title}" if list_title else ""
        lines.append(f"• {title}{hint}")
    return lines


def _format_events_reply(
    events_data: dict[str, Any],
    tasks_data: dict[str, Any] | None = None,
) -> str:
    dates = events_data.get("dates") or []
    date_label = ", ".join(dates) if dates else "указанную дату"
    calendar_rows = events_data.get("calendar_live") or events_data.get("calendar") or []
    sheet_rows = events_data.get("sheet") or []

    task_lines = _format_google_tasks_section(tasks_data or {})
    sheet_task_rows = (tasks_data or {}).get("sheets_tasks") or []

    has_events = bool(calendar_rows or sheet_rows)
    has_tasks = bool(task_lines) or bool(sheet_task_rows)

    if not has_events and not has_tasks:
        return (
            f"📅 На {date_label} в календаре нет встреч, мероприятий и задач "
            f"Google Tasks со сроком на этот день."
        )

    lines = [f"📌 На {date_label}:"]
    if task_lines:
        lines.extend(task_lines)
    elif sheet_task_rows:
        lines.append("Поручения в боте (Sheets):")
    for row in sheet_task_rows:
        title = str(row.get("title", "")).strip() or "(без названия)"
        assignee = str(row.get("assigned_to", "")).strip()
        suffix = f" ({assignee})" if assignee else ""
        lines.append(f"• {title}{suffix} — поручение в боте")

    if has_events:
        if has_tasks:
            lines.append("")
            lines.append("📅 Встречи и мероприятия:")
        else:
            lines[0] = f"📅 В календаре на {date_label}:"
        for row in calendar_rows:
            title = str(row.get("title", "")).strip() or "(без названия)"
            time_str = str(row.get("time", "")).strip()
            date_str = str(row.get("date", "")).strip()
            desc = str(row.get("description", "")).strip()
            label = str(row.get("source_calendar_label", "")).strip()
            lines.append(f"• {title}")
            if date_str:
                lines.append(f"  📅 {date_str}")
            if time_str:
                lines.append(f"  🕐 {time_str}")
            if desc:
                short = desc[:200] + ("…" if len(desc) > 200 else "")
                lines.append(f"  - {short}")
            if label:
                lines.append(f"  ({label})")
        for row in sheet_rows:
            title = str(row.get("title", "")).strip() or "(без названия)"
            time_str = str(row.get("time", "")).strip()
            suffix = f" {time_str}" if time_str else ""
            lines.append(f"• {title}{suffix} — из таблицы events")
    return "\n".join(lines)


async def try_reply_calendar_events_query(text: str) -> str | None:
    if not _looks_like_calendar_events_query(text):
        return None

    preset, dates = _detect_date_preset(text)
    if not preset:
        return None

    if settings.google_tasks_use_oauth and oauth_configured():
        if not oauth_has_calendar_read_scope():
            return (
                "Календарь пока не подключён к боту (в OAuth только Tasks).\n"
                "Запустите на сервере:\n"
                "python scripts/google_tasks_oauth_setup.py\n"
                "и снова войдите в Gmail ресторана — отметьте доступ к Calendar."
            )

    logger.info("calendar_events fast path: preset=%s dates=%s", preset, dates)
    events_data = await agent_tools.get_events_for_dates(preset=preset, dates=dates)
    tasks_data = await agent_tools.get_tasks_for_dates(preset=preset, dates=dates)
    return _format_events_reply(events_data, tasks_data)
