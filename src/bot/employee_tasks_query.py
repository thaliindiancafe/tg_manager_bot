"""Direct answers for «задачи у [имя]» / «мои задачи» without relying on LLM tool choice."""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from src.agent import tools as agent_tools
from src.config import settings
from src.google import sheets
from src.utils.employee_name_match import (
    build_name_lookup,
    inflected_name_forms,
    match_employee_name,
    nominative_candidates,
)

logger = logging.getLogger(__name__)

_TASK_WORD = re.compile(r"задач|поручен", re.IGNORECASE)
_TASK_CREATE = re.compile(
    r"добав(ь|ить)|создай|создать|постав(ь|ить)|запиши|новую\s+задач|"
    r"нужно\s+.*задач|задач[ауы]\s+для",
    re.IGNORECASE,
)
_MY_TASKS = re.compile(
    r"мои\s+задач|мои\s+поручен|у\s+меня\s+задач|какие\s+у\s+меня|"
    r"что\s+у\s+меня\s+по\s+задач|мои\s+открыт|покажи\s+мои",
    re.IGNORECASE,
)
_ALL_TASKS = re.compile(
    r"все\s+задач|все\s+поручен|в\s+принципе|вообще|открыт[ыые]?\s+задач|"
    r"список\s+задач|какие\s+есть|что\s+есть\s+у",
    re.IGNORECASE,
)
_DATE_TOMORROW = re.compile(r"\bзавтра\b", re.IGNORECASE)
_DATE_TODAY = re.compile(r"\bсегодня\b", re.IGNORECASE)
_DATE_YESTERDAY = re.compile(r"\bвчера\b", re.IGNORECASE)
_DATE_DMY = re.compile(r"\b(\d{1,2})\.(\d{1,2})(?:\.(\d{2,4}))?\b")


def _detect_date_preset(text: str) -> tuple[str, list[str] | None]:
    if _DATE_TOMORROW.search(text):
        return "tomorrow", None
    if _DATE_TODAY.search(text):
        return "today", None
    if _DATE_YESTERDAY.search(text):
        return "yesterday", None
    match = _DATE_DMY.search(text)
    if match:
        day, month = int(match.group(1)), int(match.group(2))
        year_raw = match.group(3)
        tz = ZoneInfo(settings.timezone)
        year = int(year_raw) if year_raw else datetime.now(tz).year
        if year_raw and len(year_raw) == 2:
            year = 2000 + year
        return "none", [f"{year:04d}-{month:02d}-{day:02d}"]
    return "", None


def _wants_all_open_tasks(text: str) -> bool:
    if _ALL_TASKS.search(text):
        return True
    preset, dates = _detect_date_preset(text)
    return not preset and not dates


def _wants_my_tasks(text: str) -> bool:
    return bool(_MY_TASKS.search(text))


def _find_employee_in_text(
    text: str,
    employee_names: list[str],
    *,
    lookup: dict[str, str],
) -> str | None:
    matched = match_employee_name(text, employee_names, lookup=lookup)
    if matched:
        return matched

    lowered = text.lower()
    for name in employee_names:
        if not name.strip():
            continue
        for variant in inflected_name_forms(name) + nominative_candidates(name):
            if len(variant) >= 2 and variant in lowered:
                return name.strip()

    for token in re.findall(r"[a-zA-Zа-яА-ЯёЁ]+", text):
        matched = match_employee_name(token, employee_names, lookup=lookup)
        if matched:
            return matched
    return None


async def _employee_by_telegram_user_id(telegram_user_id: int) -> str | None:
    from src.google.tasklist_resolve import is_manager_employee_row

    tid = str(int(telegram_user_id))
    for row in await sheets.read_sheet("employees"):
        if str(row.get("telegram_user_id", "")).strip() != tid:
            continue
        name = str(row.get("name", "")).strip()
        if name:
            return name

    manager_tid = (settings.google_tasks_manager_telegram_id or "").strip()
    if manager_tid and tid == manager_tid:
        for row in await sheets.read_sheet("employees"):
            if is_manager_employee_row(row):
                name = str(row.get("name", "")).strip()
                if name:
                    return name
        return settings.google_tasks_manager_name.strip() or None

    return None


def _looks_like_task_create_query(text: str) -> bool:
    return bool(_TASK_CREATE.search(text))


def _looks_like_task_list_query(text: str) -> bool:
    if _looks_like_task_create_query(text):
        return False
    return bool(_TASK_WORD.search(text))


def _format_tasks_reply(data: dict[str, Any], *, all_open: bool = False) -> str:
    name = str(data.get("employee_name", "")).strip()
    dates = data.get("dates") or []
    date_label = ", ".join(dates) if dates else "указанную дату"

    if not data.get("employee_found"):
        hint = str(data.get("hint", "")).strip()
        return hint or "Сотрудник не найден в таблице employees."

    err = data.get("google_tasks_error")
    if err:
        return f"Не удалось загрузить Google Tasks для {name}: {err}"

    google_rows = data.get("google_tasks") or []
    google_undated = data.get("google_tasks_undated") or []
    sheet_rows = data.get("sheets_tasks") or []

    if not google_rows and not google_undated and not sheet_rows:
        if all_open:
            return f"У {name} нет открытых задач в Google Tasks и в поручениях бота."
        return (
            f"У {name} нет открытых задач на {date_label} "
            f"(ни со сроком на этот день, ни без даты)."
        )

    if all_open:
        lines = [f"Все открытые задачи {name}:"]
    else:
        lines = [f"Задачи {name} на {date_label}:"]

    if google_rows:
        header = "Со сроком:" if all_open else "Со сроком на этот день:"
        lines.append(header)
        for row in google_rows:
            title = str(row.get("title", "")).strip() or "(без названия)"
            due = str(row.get("due_date", "")).strip()
            prefix = f"{due} — " if all_open and due else ""
            lines.append(f"• {prefix}{title}")
    if google_undated:
        if google_rows:
            lines.append("")
        lines.append("Без срока (Google Tasks):")
        for row in google_undated:
            title = str(row.get("title", "")).strip() or "(без названия)"
            lines.append(f"• {title}")
    if sheet_rows:
        if google_rows or google_undated:
            lines.append("")
        lines.append("Поручения в боте (Sheets):")
        for row in sheet_rows:
            title = str(row.get("title", "")).strip() or "(без названия)"
            tid = str(row.get("task_id", "")).strip()
            due = str(row.get("due_date", "")).strip()
            due_bit = f", срок {due}" if due else ""
            suffix = f" (id {tid})" if tid else ""
            lines.append(f"• {title}{due_bit}{suffix}")
    return "\n".join(lines)


async def try_reply_employee_tasks_query(
    text: str,
    *,
    telegram_user_id: int | None = None,
) -> str | None:
    """
    If the message asks for an employee's tasks, fetch and format the answer.

    Returns None when the message should go to the full agent.
    """
    if not _looks_like_task_list_query(text):
        return None

    rows = await sheets.read_sheet("employees")
    names = [str(row.get("name", "")).strip() for row in rows]
    lookup = build_name_lookup(names)

    employee: str | None = None
    if _wants_my_tasks(text) and telegram_user_id is not None:
        employee = await _employee_by_telegram_user_id(telegram_user_id)
        if not employee:
            return (
                "Не нашёл вас в справочнике сотрудников. "
                "Напишите боту /start в личке с аккаунта, с которого работаете."
            )

    if not employee:
        employee = _find_employee_in_text(text, names, lookup=lookup)
    if not employee:
        return None

    all_open = _wants_all_open_tasks(text)
    preset, dates = _detect_date_preset(text)

    if all_open:
        logger.info("employee_tasks fast path: employee=%s mode=all_open", employee)
        data = await agent_tools.get_employee_all_open_tasks(employee)
        return _format_tasks_reply(data, all_open=True)

    if not preset:
        preset = "today"

    logger.info(
        "employee_tasks fast path: employee=%s preset=%s dates=%s",
        employee,
        preset,
        dates,
    )
    data = await agent_tools.get_employee_tasks_for_dates(
        employee,
        preset=preset,
        dates=dates,
    )
    return _format_tasks_reply(data, all_open=False)
