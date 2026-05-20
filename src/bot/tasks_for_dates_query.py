"""Direct answers for «задачи на [дату]» (все списки Google Tasks), без имени сотрудника."""

from __future__ import annotations

import logging
import re
from typing import Any

from src.agent import tools as agent_tools
from src.bot.employee_tasks_query import _detect_date_preset

logger = logging.getLogger(__name__)

_TASK_WORD = re.compile(r"задач|поручен", re.IGNORECASE)
_EVENT_WORD = re.compile(
    r"встреч|мероприят|событи|план[её]рк",
    re.IGNORECASE,
)
_CALENDAR_WORD = re.compile(r"календар", re.IGNORECASE)


def _looks_like_all_tasks_query(text: str) -> bool:
    if not _TASK_WORD.search(text):
        return False
    if _EVENT_WORD.search(text) and not _TASK_WORD.search(text):
        return False
    if _CALENDAR_WORD.search(text) and _EVENT_WORD.search(text):
        return False
    preset, dates = _detect_date_preset(text)
    return bool(preset or dates)


def _format_tasks_reply(data: dict[str, Any]) -> str:
    dates = data.get("dates") or []
    date_label = ", ".join(dates) if dates else "указанную дату"

    err = data.get("google_tasks_error")
    if err:
        return f"Не удалось загрузить Google Tasks: {err}"

    google_rows = data.get("google_tasks") or []
    sheet_rows = data.get("sheets_tasks") or []

    if not google_rows and not sheet_rows:
        return f"На {date_label} нет открытых задач со сроком на этот день."

    lines = [f"Задачи на {date_label}:"]
    for row in google_rows:
        title = str(row.get("title", "")).strip() or "(без названия)"
        list_title = str(row.get("tasklist_title", "")).strip()
        hint = f" — {list_title}" if list_title else ""
        lines.append(f"• {title}{hint}")
    for row in sheet_rows:
        title = str(row.get("title", "")).strip() or "(без названия)"
        assignee = str(row.get("assigned_to", "")).strip()
        suffix = f" ({assignee})" if assignee else ""
        lines.append(f"• {title}{suffix} — поручение в боте")
    return "\n".join(lines)


async def try_reply_tasks_for_dates_query(text: str) -> str | None:
    if not _looks_like_all_tasks_query(text):
        return None

    preset, dates = _detect_date_preset(text)
    if not preset:
        return None

    logger.info("tasks_for_dates fast path: preset=%s dates=%s", preset, dates)
    data = await agent_tools.get_tasks_for_dates(preset=preset, dates=dates)
    return _format_tasks_reply(data)
