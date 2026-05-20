"""Gemini tool-use wrappers over Google integrations."""

from __future__ import annotations

import json
import logging
import os
import re
import time
import uuid
from datetime import datetime, timedelta
from typing import Any

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from zoneinfo import ZoneInfo

from src.agent.checklist_proof import (
    all_items_ok,
    format_proof_summary_ru,
    parse_checklist_evaluation_response,
)
from src.agent.task_status import (
    PENDING_PROOF_FACT_PREFIX,
    PROOF_REPORT_MARKER,
    TASK_STATUS_AWAITING_PROOF,
    TASK_STATUS_DONE,
    TASK_STATUS_REVIEW,
    is_closed_status,
    normalize_status,
)
from src.config import settings
from src.utils.employee_name_match import match_employee_name
from src.utils.employee_role_resolve import (
    EmployeeResolveResult,
    format_staff_directory,
    resolve_employee_reference,
)
from src.utils.schedule_shift_resolve import (
    is_shift_unpacking_query,
    resolve_shift_unpacking_from_schedule,
)
from src.google import calendar as google_calendar
from src.google.calendar_targets import (
    calendars_for_read,
    events_calendar_id,
)
from src.google import drive as google_drive
from src.google import sheets
from src.google import tasks as google_tasks
from src.google.oauth_credentials import oauth_configured

logger = logging.getLogger(__name__)

_agent_bot: Bot | None = None

_GROUP_NOTICE_COOLDOWN_EMPLOYEE = "_system_group_notice_cooldown"


def configure_tool_bot(bot: Bot | None) -> None:
    """Bind Telegram bot for tools that send private messages (call from main on startup)."""
    global _agent_bot
    _agent_bot = bot


def _require_bot() -> Bot:
    if _agent_bot is None:
        raise RuntimeError("Telegram Bot is not configured for tools")
    return _agent_bot


def _normalize_username(value: str) -> str:
    return (value or "").strip().lstrip("@").lower()


async def _employee_row_by_telegram_id(
    telegram_user_id: str,
) -> tuple[int | None, dict[str, Any] | None]:
    tid = str(telegram_user_id).strip()
    rows = await sheets.read_sheet("employees")
    for offset, row in enumerate(rows):
        if str(row.get("telegram_user_id", "")).strip() == tid:
            return offset + 2, row
    return None, None


async def _resolve_employee_row(
    query: str,
) -> tuple[int | None, dict[str, Any] | None, EmployeeResolveResult]:
    q = query.strip()
    if not q:
        return None, None, EmployeeResolveResult(
            ok=False, error="Пустой идентификатор сотрудника"
        )
    rows = await sheets.read_sheet("employees")

    if is_shift_unpacking_query(q):
        result = await resolve_shift_unpacking_from_schedule(rows)
    else:
        result = resolve_employee_reference(q, rows)

    if not result.ok:
        return None, None, result
    target = result.canonical_name.strip().lower()
    for offset, row in enumerate(rows):
        if str(row.get("name", "")).strip().lower() == target:
            return offset + 2, row, result
    return None, None, EmployeeResolveResult(
        ok=False, error="Строка employees не найдена после сопоставления"
    )


async def _employee_row_by_name_insensitive(
    name: str,
) -> tuple[int | None, dict[str, Any] | None]:
    row_idx, row, result = await _resolve_employee_row(name)
    if not result.ok:
        return None, None
    return row_idx, row


async def build_staff_roles_section() -> str:
    """Block for system prompt: names, roles, @username."""
    try:
        rows = await sheets.read_sheet("employees")
        body = format_staff_directory(rows)
        return (
            "## Справочник сотрудников (employees)\n"
            f"{body}\n\n"
            "Для **send_dm_to_employee**, **delegate_private_reminder**, **create_task** "
            "в поле имени можно передать: **имя** (Гири), **@username** (Girl5719) "
            "или **должность** (су-шеф, шеф, бариста…). "
            "Должности — колонка **role** в employees. "
            "**Помощник на смене** / разбор товара — кто сегодня в графике (schedule, блок "
            f"{getattr(settings, 'schedule_unpacking_roles', 'Kleener')}), не фиксированное имя."
        )
    except Exception as exc:
        logger.warning("build_staff_roles_section failed: %s", exc)
        return ""


async def get_employee_directory() -> dict[str, Any]:
    """
    Список активных сотрудников: имя, должность (role), username, есть ли Telegram.

    Вызывай, если нужно понять, кому поручение (шеф, су-шеф) или кого нет в таблице.
    """
    try:
        rows = await sheets.read_sheet("employees")
        staff: list[dict[str, Any]] = []
        for row in rows:
            if str(row.get("active", "true")).strip().lower() in {
                "false",
                "0",
                "no",
                "нет",
            }:
                continue
            name = str(row.get("name", "")).strip()
            if not name:
                continue
            staff.append(
                {
                    "name": name,
                    "role": str(row.get("role", "")).strip(),
                    "username": str(row.get("username", "")).strip(),
                    "telegram_linked": bool(
                        str(row.get("telegram_user_id", "")).strip()
                    ),
                }
            )
        return {
            "employees": staff,
            "hint": (
                "Поручение: delegate_private_reminder(employee_name='су-шеф'|'Гири'|'помощник на смене', …). "
                "role в employees: Гири→су-шеф, Пракаш→шеф. "
                "«помощник на смене» — из графика на сегодня (schedule, Kleener)."
            ),
        }
    except Exception as exc:
        logger.error("get_employee_directory failed: %s", exc, exc_info=True)
        raise


async def _employee_rows_by_username(
    username: str,
) -> list[tuple[int, dict[str, Any]]]:
    """(1-based sheet row index, row dict) for rows whose username column matches."""
    u = _normalize_username(username)
    if not u:
        return []
    rows = await sheets.read_sheet("employees")
    out: list[tuple[int, dict[str, Any]]] = []
    for offset, row in enumerate(rows):
        cell = _normalize_username(str(row.get("username", "")))
        if cell == u:
            out.append((offset + 2, row))
    return out


_GOOGLE_TASK_ID_RE = re.compile(r"google_task_id:([^\s\n]+)")
_SCHEDULE_DATE_ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _now_local_str() -> str:
    return datetime.now(ZoneInfo(settings.timezone)).strftime("%Y-%m-%d %H:%M:%S")


def _today_local_str() -> str:
    return datetime.now(ZoneInfo(settings.timezone)).strftime("%Y-%m-%d")


def _due_date_to_rfc3339(due_date: str) -> str:
    text = due_date.strip()
    if "T" in text:
        return text if text.endswith("Z") else f"{text}Z"
    return f"{text}T00:00:00Z"


def _extract_google_task_id(notes: str) -> str | None:
    match = _GOOGLE_TASK_ID_RE.search(notes or "")
    return match.group(1) if match else None


def _append_google_task_id(notes: str, google_task_id: str) -> str:
    marker = f"google_task_id:{google_task_id}"
    base = (notes or "").strip()
    if marker in base:
        return base
    return f"{base}\n{marker}".strip() if base else marker


_DELEGATION_MARKER = "__DELEGATION_JSON__"


def _normalize_checklist_items(raw: list[str] | None) -> list[str]:
    if raw is None:
        return []
    if not isinstance(raw, (list, tuple)):
        return []
    return [str(x).strip() for x in raw if str(x).strip()]


def _append_delegation_json_block(notes: str, checklist: list[str]) -> str:
    blob = json.dumps(
        {"delegation": {"version": 1, "checklist": checklist}},
        ensure_ascii=False,
    )
    base = (notes or "").strip()
    block = f"{_DELEGATION_MARKER}\n{blob}"
    if not base:
        return block
    return f"{base}\n\n{block}"


def _build_delegate_task_notes(
    message_to_employee: str,
    notes_for_task: str,
    checklist_items: list[str] | None,
) -> str:
    nt = (notes_for_task or "").strip()
    if nt:
        base = nt
    else:
        base = (message_to_employee or "").strip()[:4000]
    items = _normalize_checklist_items(checklist_items)
    if items:
        return _append_delegation_json_block(base, items)[:8000]
    return base[:8000]


def parse_delegation_from_notes(notes: str) -> dict[str, Any] | None:
    """Parse embedded delegation JSON from task notes (used for open-task summaries)."""
    text = notes or ""
    if _DELEGATION_MARKER not in text:
        return None
    tail = text.split(_DELEGATION_MARKER, 1)[1].strip()
    try:
        data = json.loads(tail)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _delegation_checklist_from_notes(notes: str) -> list[str]:
    data = parse_delegation_from_notes(notes)
    if not data:
        return []
    inner = data.get("delegation")
    if not isinstance(inner, dict):
        return []
    raw = inner.get("checklist")
    if not isinstance(raw, list):
        return []
    return [str(x).strip() for x in raw if str(x).strip()]


def _append_proof_report_block(notes: str, report: dict[str, Any]) -> str:
    blob = json.dumps({"proof_report": report}, ensure_ascii=False)
    base = (notes or "").strip()
    block = f"{PROOF_REPORT_MARKER}\n{blob}"
    if not base:
        return block
    return f"{base}\n\n{block}"[:12000]


async def _set_task_status(task_id: str, status: str) -> None:
    row_index, row = await _find_sheet_row_index("tasks", "task_id", task_id)
    if row is None or row_index is None:
        raise ValueError(f"Задача с task_id={task_id!r} не найдена")
    updated = dict(row)
    updated["status"] = status
    await sheets.update_row("tasks", row_index, updated)


async def set_pending_proof_task(telegram_user_id: int, task_id: str) -> None:
    key = f"{PENDING_PROOF_FACT_PREFIX}{int(telegram_user_id)}"
    await sheets.upsert_memory_fact_row(key, str(task_id).strip())


async def pop_pending_proof_task(telegram_user_id: int) -> str | None:
    key = f"{PENDING_PROOF_FACT_PREFIX}{int(telegram_user_id)}"
    facts = await sheets.read_sheet("memory_facts")
    for row in facts:
        if str(row.get("employee", "")).strip() == key:
            tid = str(row.get("fact", "")).strip()
            if tid:
                await sheets.upsert_memory_fact_row(key, "")
            return tid or None
    return None


async def resolve_single_open_task_id(telegram_user_id: int) -> str | None:
    data = await get_open_tasks_for_telegram_user(telegram_user_id)
    tasks = data.get("open_tasks") or []
    if len(tasks) == 1:
        return str(tasks[0].get("task_id", "")).strip() or None
    return None


async def _find_sheet_row_index(
    sheet_name: str,
    column: str,
    value: str,
) -> tuple[int | None, dict[str, Any] | None]:
    rows = await sheets.read_sheet(sheet_name)
    target = str(value).strip()
    for offset, row in enumerate(rows):
        if str(row.get(column, "")).strip() == target:
            return offset + 2, row
    return None, None


async def _get_employee_tasklist_id(
    employee_name: str,
    *,
    auto_create: bool = True,
) -> str | None:
    from src.google.tasklist_resolve import (
        ensure_tasklist_for_employee_row,
        resolve_tasklist_for_employee_row,
    )

    row_idx, row = await _employee_row_by_name_insensitive(employee_name)
    if not row:
        return None
    if auto_create and row_idx is not None:
        resolved = await ensure_tasklist_for_employee_row(row_idx, row)
        if resolved:
            return resolved[0]
    resolved = await resolve_tasklist_for_employee_row(row)
    return resolved[0] if resolved else None


async def _sync_google_task_complete(assigned_to: str, notes: str) -> None:
    google_task_id = _extract_google_task_id(notes)
    if not google_task_id:
        return

    tasklist_id = await _get_employee_tasklist_id(assigned_to)
    if not tasklist_id:
        logger.info(
            "Google Tasks sync skipped on complete: no tasklist for %s",
            assigned_to,
        )
        return

    await google_tasks.complete_task(tasklist_id, google_task_id)


async def _sync_google_task_deadline(
    assigned_to: str,
    notes: str,
    new_due_date: str,
) -> None:
    google_task_id = _extract_google_task_id(notes)
    if not google_task_id:
        return

    tasklist_id = await _get_employee_tasklist_id(assigned_to)
    if not tasklist_id:
        logger.info(
            "Google Tasks sync skipped on postpone: no tasklist for %s",
            assigned_to,
        )
        return

    await google_tasks.update_task_deadline(
        tasklist_id,
        google_task_id,
        _due_date_to_rfc3339(new_due_date),
    )


def _normalize_schedule_date_cell(value: Any) -> str:
    return str(value or "").strip()[:10]


def _collect_schedule_target_dates(preset: str, dates: list[str] | None) -> set[str]:
    tz = ZoneInfo(settings.timezone)
    today = datetime.now(tz).date()
    out: set[str] = set()
    p = (preset or "today").strip().lower()
    if p not in {"today", "tomorrow", "yesterday", "none"}:
        p = "today"
    if p == "today":
        out.add(today.isoformat())
    elif p == "tomorrow":
        out.add((today + timedelta(days=1)).isoformat())
    elif p == "yesterday":
        out.add((today - timedelta(days=1)).isoformat())
    for raw in dates or []:
        s = str(raw).strip()[:10]
        if _SCHEDULE_DATE_ISO_RE.fullmatch(s):
            out.add(s)
    if not out:
        out.add(today.isoformat())
    return out


def schedule_target_dates(
    preset: str = "today",
    dates: list[str] | None = None,
) -> list[str]:
    """Resolved YYYY-MM-DD list for schedule/task date presets."""
    return sorted(_collect_schedule_target_dates(preset, dates))


async def get_schedule_for_dates(
    preset: str = "today",
    dates: list[str] | None = None,
) -> list[dict[str, Any]]:
    """
    Смены из листа schedule только для указанных календарных дней.

    preset: today | tomorrow | yesterday | none — относительный день в часовом поясе приложения.
    dates: дополнительные даты в формате YYYY-MM-DD (например конкретный день недели).

    Используй для «кто сегодня на смене» → preset=today;
    «кто завтра» → preset=tomorrow; «кто вчера» → preset=yesterday;
    за неделю — preset=none и несколько дат в dates. Не смешивай дни: возвращай только запрошенные.
    """
    try:
        targets = _collect_schedule_target_dates(preset, dates)
        schedule = await sheets.read_sheet("schedule")
        return [
            row
            for row in schedule
            if _normalize_schedule_date_cell(row.get("date")) in targets
        ]
    except Exception as exc:
        logger.error(
            "get_schedule_for_dates failed: preset=%s dates=%s error=%s",
            preset,
            dates,
            exc,
            exc_info=True,
        )
        raise


async def get_tasks_for_dates(
    preset: str = "today",
    dates: list[str] | None = None,
) -> dict[str, Any]:
    """
    Задачи на указанные дни: Google Tasks (как в календаре Gmail) + лист tasks в Sheets.

    preset: today | tomorrow | yesterday | none — как get_schedule_for_dates.
    dates: дополнительные YYYY-MM-DD.

    Используй для «какие задачи на завтра», «что на 19.05», «задачи на сегодня».
    Это НЕ мероприятия календаря — для встреч/событий вызывай get_events_for_dates.
    """
    try:
        targets = sorted(_collect_schedule_target_dates(preset, dates))
        target_set = set(targets)

        google_tasks_rows: list[dict[str, Any]] = []
        google_error: str | None = None
        if settings.google_tasks_use_oauth and oauth_configured():
            try:
                google_tasks_rows = await google_tasks.list_open_tasks_for_dates(
                    targets
                )
            except Exception as exc:
                google_error = str(exc)
                logger.error(
                    "get_tasks_for_dates Google Tasks failed: %s",
                    exc,
                    exc_info=True,
                )
        else:
            google_error = (
                "Google Tasks OAuth не настроен — в календаре видны только "
                "после scripts/google_tasks_oauth_setup.py"
            )

        sheet_rows: list[dict[str, Any]] = []
        for row in await sheets.read_sheet("tasks"):
            due = _normalize_schedule_date_cell(row.get("due_date"))
            if due not in target_set:
                continue
            if is_closed_status(str(row.get("status", "")).strip()):
                continue
            sheet_rows.append(row)

        return {
            "dates": targets,
            "google_tasks": google_tasks_rows,
            "google_tasks_error": google_error,
            "sheets_tasks": sheet_rows,
        }
    except Exception as exc:
        logger.error(
            "get_tasks_for_dates failed: preset=%s dates=%s error=%s",
            preset,
            dates,
            exc,
            exc_info=True,
        )
        raise


async def get_employee_tasks_for_dates(
    employee_name: str,
    preset: str = "today",
    dates: list[str] | None = None,
) -> dict[str, Any]:
    """
    Задачи одного сотрудника на указанные дни: его список Google Tasks + лист tasks.

    employee_name — как в employees.name («Ира», «Августа»).
    preset: today | tomorrow | yesterday | none; dates — YYYY-MM-DD.

    Используй для «какие задачи у Иры на завтра», «что у Петрова на 19.05».
    Не вызывай get_employee_tasks без даты — он только таблица бота, без Google Tasks.
    """
    try:
        targets = sorted(_collect_schedule_target_dates(preset, dates))
        target_set = set(targets)

        _, emp_row = await _employee_row_by_name_insensitive(employee_name)
        if not emp_row:
            return {
                "employee_found": False,
                "employee_name": employee_name.strip(),
                "dates": targets,
                "hint": (
                    f"Сотрудник {employee_name!r} не найден в листе employees. "
                    "Имя должно совпадать с колонкой name."
                ),
                "google_tasks": [],
                "sheets_tasks": [],
            }

        from src.google.tasklist_resolve import resolve_tasklist_for_employee_row

        canonical_name = str(emp_row.get("name", "")).strip()
        resolved = await resolve_tasklist_for_employee_row(emp_row)
        tasklist_id = resolved[0] if resolved else ""
        list_title = resolved[1] if resolved else canonical_name

        google_rows: list[dict[str, Any]] = []
        google_undated: list[dict[str, Any]] = []
        google_error: str | None = None
        if settings.google_tasks_use_oauth and oauth_configured():
            try:
                if tasklist_id:
                    google_rows, google_undated = (
                        await google_tasks.list_open_tasks_in_tasklist_for_dates(
                            tasklist_id,
                            list_title,
                            targets,
                            include_undated=True,
                        )
                    )
                else:
                    all_rows = await google_tasks.list_open_tasks_for_dates(targets)
                    name_key = canonical_name.strip().lower()
                    google_rows = [
                        row
                        for row in all_rows
                        if str(row.get("tasklist_title", "")).strip().lower()
                        == name_key
                    ]
            except Exception as exc:
                google_error = str(exc)
                logger.error(
                    "get_employee_tasks_for_dates Google Tasks failed: employee=%s error=%s",
                    employee_name,
                    exc,
                    exc_info=True,
                )
        else:
            google_error = "Google Tasks OAuth не настроен"

        sheet_rows: list[dict[str, Any]] = []
        name_key = canonical_name.strip().lower()
        for row in await sheets.read_sheet("tasks"):
            assigned = str(row.get("assigned_to", "")).strip().lower()
            if assigned != name_key:
                continue
            due = _normalize_schedule_date_cell(row.get("due_date"))
            if due not in target_set:
                continue
            if is_closed_status(str(row.get("status", "")).strip()):
                continue
            sheet_rows.append(row)

        return {
            "employee_found": True,
            "employee_name": canonical_name,
            "tasklist_id": tasklist_id,
            "dates": targets,
            "google_tasks": google_rows,
            "google_tasks_undated": google_undated,
            "google_tasks_error": google_error,
            "sheets_tasks": sheet_rows,
        }
    except Exception as exc:
        logger.error(
            "get_employee_tasks_for_dates failed: employee=%s preset=%s error=%s",
            employee_name,
            preset,
            exc,
            exc_info=True,
        )
        raise


async def get_employee_all_open_tasks(employee_name: str) -> dict[str, Any]:
    """
    Все открытые задачи сотрудника: Google Tasks (весь список) + лист tasks без фильтра даты.

    Используй для «какие задачи у Иры», «все задачи Августы», «мои задачи» (после определения имени).
    """
    try:
        _, emp_row = await _employee_row_by_name_insensitive(employee_name)
        if not emp_row:
            return {
                "employee_found": False,
                "employee_name": employee_name.strip(),
                "hint": (
                    f"Сотрудник {employee_name!r} не найден в листе employees. "
                    "Имя должно совпадать с колонкой name."
                ),
                "google_tasks": [],
                "google_tasks_undated": [],
                "sheets_tasks": [],
            }

        from src.google.tasklist_resolve import resolve_tasklist_for_employee_row

        canonical_name = str(emp_row.get("name", "")).strip()
        resolved = await resolve_tasklist_for_employee_row(emp_row)
        tasklist_id = resolved[0] if resolved else ""
        list_title = resolved[1] if resolved else canonical_name

        google_dated: list[dict[str, Any]] = []
        google_undated: list[dict[str, Any]] = []
        google_error: str | None = None
        if settings.google_tasks_use_oauth and oauth_configured():
            try:
                if tasklist_id:
                    google_dated, google_undated = (
                        await google_tasks.list_all_open_tasks_in_tasklist(
                            tasklist_id,
                            list_title,
                        )
                    )
                else:
                    name_key = canonical_name.strip().lower()
                    for tl in await google_tasks.list_tasklists():
                        title = str(tl.get("title", "")).strip()
                        if title.strip().lower() != name_key:
                            continue
                        tl_id = str(tl.get("id", "")).strip()
                        if not tl_id:
                            continue
                        dated, undated = await google_tasks.list_all_open_tasks_in_tasklist(
                            tl_id,
                            title,
                        )
                        google_dated.extend(dated)
                        google_undated.extend(undated)
                        break
            except Exception as exc:
                google_error = str(exc)
                logger.error(
                    "get_employee_all_open_tasks Google Tasks failed: %s",
                    exc,
                    exc_info=True,
                )
        else:
            google_error = "Google Tasks OAuth не настроен"

        name_key = canonical_name.strip().lower()
        sheet_rows: list[dict[str, Any]] = []
        for row in await sheets.read_sheet("tasks"):
            if str(row.get("assigned_to", "")).strip().lower() != name_key:
                continue
            if is_closed_status(str(row.get("status", "")).strip()):
                continue
            sheet_rows.append(row)

        return {
            "employee_found": True,
            "employee_name": canonical_name,
            "tasklist_id": tasklist_id,
            "mode": "all_open",
            "google_tasks": google_dated,
            "google_tasks_undated": google_undated,
            "google_tasks_error": google_error,
            "sheets_tasks": sheet_rows,
        }
    except Exception as exc:
        logger.error(
            "get_employee_all_open_tasks failed: employee=%s error=%s",
            employee_name,
            exc,
            exc_info=True,
        )
        raise


async def get_employee_tasks(employee_name: str) -> list[dict[str, Any]]:
    """
    Все задачи сотрудника из таблицы tasks (Sheets), без фильтра по дате.

    Для Google Tasks на дату — get_employee_tasks_for_dates.
    Для всех открытых — get_employee_all_open_tasks.
    Для всех сотрудников на день — get_tasks_for_dates.
    """
    try:
        name = employee_name.strip().lower()
        tasks = await sheets.read_sheet("tasks")
        return [
            row
            for row in tasks
            if str(row.get("assigned_to", "")).strip().lower() == name
        ]
    except Exception as exc:
        logger.error(
            "get_employee_tasks failed: employee=%s error=%s",
            employee_name,
            exc,
            exc_info=True,
        )
        raise


async def get_open_tasks_for_telegram_user(
    telegram_user_id: int,
) -> dict[str, Any]:
    """
    Открытые задачи сотрудника по его telegram_user_id (лист tasks + employees).

    Статусы done/cancelled не включаются. Для каждой задачи возвращается task_id, title,
    due_date, status и пункты чеклиста из notes (если поручение создавали с checklist).
    Используй в личке, когда человек отчитывается без реплая и нужно понять, о какой
    задаче речь, или чтобы перечислить варианты и попросить выбрать task_id.
    """
    try:
        tid = str(int(telegram_user_id))
        _, row = await _employee_row_by_telegram_id(tid)
        if not row:
            return {
                "employee_found": False,
                "hint": "Нет строки в employees с этим telegram_user_id — нужен /start в боте.",
                "open_tasks": [],
            }
        name = str(row.get("name", "")).strip()
        if not name:
            return {
                "employee_found": False,
                "hint": "В employees пустое имя для этого telegram_user_id.",
                "open_tasks": [],
            }
        target = name.strip().lower()
        tasks = await sheets.read_sheet("tasks")
        open_tasks: list[dict[str, Any]] = []
        for t in tasks:
            assigned = str(t.get("assigned_to", "")).strip().lower()
            if assigned != target:
                continue
            st = str(t.get("status", "")).strip()
            if is_closed_status(st):
                continue
            notes = str(t.get("notes", ""))
            open_tasks.append(
                {
                    "task_id": str(t.get("task_id", "")).strip(),
                    "title": str(t.get("title", "")).strip(),
                    "due_date": str(t.get("due_date", "")).strip(),
                    "status": str(t.get("status", "")).strip(),
                    "checklist": _delegation_checklist_from_notes(notes),
                }
            )
        return {
            "employee_found": True,
            "employee_name": name,
            "open_tasks": open_tasks,
        }
    except Exception as exc:
        logger.error(
            "get_open_tasks_for_telegram_user failed: telegram_user_id=%s error=%s",
            telegram_user_id,
            exc,
            exc_info=True,
        )
        raise


async def assert_task_for_telegram_user(
    task_id: str,
    telegram_user_id: int,
) -> dict[str, Any]:
    """
    Проверить, что задача существует и назначена на сотрудника с данным telegram_user_id.

    Вызывай перед complete_task, когда отчёт пришёл **реплаем** на поручение и в контексте
    уже указан task_id — чтобы не закрыть чужую задачу.
    """
    try:
        tid = str(task_id).strip()
        if not tid:
            return {"ok": False, "error": "Пустой task_id"}

        uid = str(int(telegram_user_id))
        _, emp = await _employee_row_by_telegram_id(uid)
        if not emp:
            return {
                "ok": False,
                "error": "Сотрудник не найден в employees по telegram_user_id.",
            }
        name = str(emp.get("name", "")).strip()
        if not name:
            return {
                "ok": False,
                "error": "В employees пустое имя для этого telegram_user_id.",
            }

        _, task_row = await _find_sheet_row_index("tasks", "task_id", tid)
        if not task_row:
            return {"ok": False, "error": "Задача с таким task_id не найдена."}

        assigned = str(task_row.get("assigned_to", "")).strip().lower()
        if assigned != name.strip().lower():
            return {
                "ok": False,
                "error": "Задача назначена другому сотруднику; закрыть её этому Telegram-пользователю нельзя.",
                "assigned_to": str(task_row.get("assigned_to", "")).strip(),
            }

        return {
            "ok": True,
            "task_id": tid,
            "title": str(task_row.get("title", "")).strip(),
            "status": str(task_row.get("status", "")).strip(),
            "employee_name": name,
        }
    except Exception as exc:
        logger.error(
            "assert_task_for_telegram_user failed: task_id=%s telegram_user_id=%s error=%s",
            task_id,
            telegram_user_id,
            exc,
            exc_info=True,
        )
        raise


async def complete_task(task_id: str) -> dict[str, Any]:
    """
    Отметить задачу выполненной (status=done) в таблице tasks.

    Если у задачи есть чеклист в notes — закрывать только из статуса review
    (после проверки) или если чеклиста нет. Иначе сначала submit_task_proof.
    Если задача синхронизирована с Google Tasks, завершить и там.
    """
    try:
        row_index, row = await _find_sheet_row_index("tasks", "task_id", task_id)
        if row is None or row_index is None:
            raise ValueError(f"Задача с task_id={task_id!r} не найдена")

        notes = str(row.get("notes", ""))
        checklist = _delegation_checklist_from_notes(notes)
        st = normalize_status(str(row.get("status", "")))
        if checklist and st not in (TASK_STATUS_REVIEW,):
            if st == TASK_STATUS_AWAITING_PROOF:
                raise ValueError(
                    "По этому поручению нужен отчёт с фото или текстом (submit_task_proof), "
                    "закрыть одним словом нельзя."
                )
            raise ValueError(
                f"Задача с чеклистом в статусе {st!r}: дождитесь проверки (review) "
                "или используйте approve_task."
            )

        updated = dict(row)
        updated["status"] = TASK_STATUS_DONE
        await sheets.update_row("tasks", row_index, updated)

        assigned_to = str(row.get("assigned_to", "")).strip()
        notes = str(row.get("notes", ""))
        try:
            await _sync_google_task_complete(assigned_to, notes)
        except Exception as sync_exc:
            logger.warning(
                "Google Tasks sync failed on complete_task: task_id=%s error=%s",
                task_id,
                sync_exc,
            )

        return {"task_id": task_id, "status": TASK_STATUS_DONE}
    except Exception as exc:
        logger.error(
            "complete_task failed: task_id=%s error=%s",
            task_id,
            exc,
            exc_info=True,
        )
        raise


async def _notify_task_in_review(
    task_id: str,
    title: str,
    assigned_to: str,
    summary: str,
) -> None:
    brief = (
        f"Поручение на проверке: **{title}** ({assigned_to})\n"
        f"{summary}\n"
        f"ID: `{task_id}` — одобрить: approve_task, вернуть: reject_task_proof."
    )
    try:
        await send_brief_to_primary_work_chat(brief[:450])
    except Exception as exc:
        logger.warning("notify_task_in_review failed: task_id=%s %s", task_id, exc)


async def _apply_proof_evaluation(
    task_id: str,
    row_index: int,
    row: dict[str, Any],
    checklist: list[str],
    proof_text: str,
    *,
    source: str,
    telegram_user_id: int | None = None,
) -> dict[str, Any]:
    from src.agent.client import evaluate_checklist_proof_text

    items: list[dict[str, str]] = []
    if checklist:
        eval_raw = await evaluate_checklist_proof_text(proof_text, checklist)
        items = parse_checklist_evaluation_response(eval_raw, checklist)
        all_ok = all_items_ok(items)
        new_status = TASK_STATUS_REVIEW if all_ok else TASK_STATUS_AWAITING_PROOF
    else:
        all_ok = True
        new_status = TASK_STATUS_REVIEW

    report: dict[str, Any] = {
        "version": 1,
        "submitted_at": _now_local_str(),
        "source": source,
        "submitted_by_telegram_id": str(telegram_user_id or ""),
        "items": items,
        "all_ok": all_ok,
    }
    notes = _append_proof_report_block(str(row.get("notes", "")), report)
    updated = dict(row)
    updated["notes"] = notes
    updated["status"] = new_status
    await sheets.update_row("tasks", row_index, updated)

    summary = format_proof_summary_ru(items) if items else "Отчёт принят, ожидает проверки."
    result: dict[str, Any] = {
        "ok": True,
        "task_id": task_id,
        "status": new_status,
        "all_ok": all_ok,
        "summary": summary,
        "checklist_results": items,
    }
    if new_status == TASK_STATUS_REVIEW:
        await _notify_task_in_review(
            task_id,
            str(row.get("title", "")).strip(),
            str(row.get("assigned_to", "")).strip(),
            summary,
        )
        result["message"] = (
            "Отчёт принят. Задача на проверке у руководителя — закрыть самому нельзя."
        )
    else:
        result["message"] = (
            "Не все пункты чеклиста подтверждены. Дослать фото или уточнить отчёт."
        )
    return result


async def submit_task_proof(
    task_id: str,
    proof_description: str,
    telegram_user_id: int,
) -> dict[str, Any]:
    """
    Принять текстовый отчёт сотрудника по задаче и сверить с чеклистом из notes.

    При полном совпадении пунктов — status=review и уведомление в рабочий чат;
    иначе awaiting_proof. Вызывай после assert_task_for_telegram_user.
    """
    try:
        check = await assert_task_for_telegram_user(task_id, telegram_user_id)
        if not check.get("ok"):
            return check

        row_index, row = await _find_sheet_row_index("tasks", "task_id", task_id.strip())
        if row is None or row_index is None:
            return {"ok": False, "error": "Задача не найдена"}

        checklist = _delegation_checklist_from_notes(str(row.get("notes", "")))
        proof_text = (proof_description or "").strip()
        if not proof_text:
            return {"ok": False, "error": "Пустой отчёт (proof_description)."}

        return await _apply_proof_evaluation(
            task_id.strip(),
            row_index,
            row,
            checklist,
            proof_text,
            source="text",
            telegram_user_id=telegram_user_id,
        )
    except Exception as exc:
        logger.error(
            "submit_task_proof failed: task_id=%s error=%s",
            task_id,
            exc,
            exc_info=True,
        )
        raise


async def submit_task_proof_from_image(
    task_id: str,
    telegram_user_id: int,
    image_base64: str,
    mime_type: str = "image/jpeg",
    caption: str = "",
) -> dict[str, Any]:
    """Photo proof path (handler); same checklist logic as submit_task_proof."""
    try:
        from src.agent.client import describe_photo

        check = await assert_task_for_telegram_user(task_id, telegram_user_id)
        if not check.get("ok"):
            return check

        row_index, row = await _find_sheet_row_index("tasks", "task_id", task_id.strip())
        if row is None or row_index is None:
            return {"ok": False, "error": "Задача не найдена"}

        checklist = _delegation_checklist_from_notes(str(row.get("notes", "")))
        vision_text = await describe_photo(image_base64, mime_type=mime_type)
        proof_text = vision_text
        cap = (caption or "").strip()
        if cap:
            proof_text = f"Подпись сотрудника: {cap}\n\nОписание фото:\n{vision_text}"

        return await _apply_proof_evaluation(
            task_id.strip(),
            row_index,
            row,
            checklist,
            proof_text,
            source="image",
            telegram_user_id=telegram_user_id,
        )
    except Exception as exc:
        logger.error(
            "submit_task_proof_from_image failed: task_id=%s error=%s",
            task_id,
            exc,
            exc_info=True,
        )
        raise


async def approve_task(task_id: str) -> dict[str, Any]:
    """Руководитель: одобрить отчёт — status review → done."""
    try:
        row_index, row = await _find_sheet_row_index("tasks", "task_id", task_id.strip())
        if row is None or row_index is None:
            return {"ok": False, "error": "Задача не найдена"}

        st = normalize_status(str(row.get("status", "")))
        if st != TASK_STATUS_REVIEW:
            return {
                "ok": False,
                "error": f"Задача не на проверке (статус {st!r}); approve только из review.",
            }

        updated = dict(row)
        updated["status"] = TASK_STATUS_DONE
        await sheets.update_row("tasks", row_index, updated)

        assigned_to = str(row.get("assigned_to", "")).strip()
        notes = str(row.get("notes", ""))
        try:
            await _sync_google_task_complete(assigned_to, notes)
        except Exception as sync_exc:
            logger.warning(
                "Google Tasks sync failed on approve_task: task_id=%s error=%s",
                task_id,
                sync_exc,
            )

        return {"ok": True, "task_id": task_id.strip(), "status": TASK_STATUS_DONE}
    except Exception as exc:
        logger.error(
            "approve_task failed: task_id=%s error=%s",
            task_id,
            exc,
            exc_info=True,
        )
        raise


async def reject_task_proof(task_id: str, comment: str = "") -> dict[str, Any]:
    """
    Руководитель: вернуть поручение на доработку — review → awaiting_proof, комментарий в notes.
    """
    try:
        row_index, row = await _find_sheet_row_index("tasks", "task_id", task_id.strip())
        if row is None or row_index is None:
            return {"ok": False, "error": "Задача не найдена"}

        st = normalize_status(str(row.get("status", "")))
        if st != TASK_STATUS_REVIEW:
            return {
                "ok": False,
                "error": f"Отклонить отчёт можно только из review (сейчас {st!r}).",
            }

        notes = str(row.get("notes", "")).strip()
        line = f"[reject {_now_local_str()}] {(comment or '').strip()}"
        if line.strip() and line not in notes:
            notes = f"{notes}\n{line}".strip() if notes else line

        updated = dict(row)
        updated["notes"] = notes[:12000]
        updated["status"] = TASK_STATUS_AWAITING_PROOF
        await sheets.update_row("tasks", row_index, updated)

        assigned_to = str(row.get("assigned_to", "")).strip()
        if assigned_to:
            dm = (
                f"Поручение «{str(row.get('title', '')).strip()}» возвращено на доработку.\n"
                f"{(comment or '').strip()}\n"
                f"ID: `{task_id.strip()}` — пришлите новый отчёт реплаем на сообщение бота."
            )
            try:
                await send_dm_to_employee(assigned_to, dm[:3500])
            except Exception as dm_exc:
                logger.warning("reject_task_proof DM failed: %s", dm_exc)

        return {
            "ok": True,
            "task_id": task_id.strip(),
            "status": TASK_STATUS_AWAITING_PROOF,
        }
    except Exception as exc:
        logger.error(
            "reject_task_proof failed: task_id=%s error=%s",
            task_id,
            exc,
            exc_info=True,
        )
        raise


def format_proof_result_message(result: dict[str, Any]) -> str:
    if not result.get("ok", True) and result.get("error"):
        return str(result.get("error"))
    msg = str(result.get("message", "")).strip()
    summary = str(result.get("summary", "")).strip()
    parts = [p for p in (msg, summary) if p]
    return "\n\n".join(parts) if parts else "Отчёт обработан."


async def postpone_task(task_id: str, new_due_date: str) -> dict[str, Any]:
    """
    Перенести дедлайн задачи (колонка due_date) в таблице tasks.

    new_due_date в формате YYYY-MM-DD.
    При наличии синхронизации обновляет дедлайн и в Google Tasks.
    Используй, когда просят перенести, отложить или сдвинуть срок задачи.
    """
    try:
        row_index, row = await _find_sheet_row_index("tasks", "task_id", task_id)
        if row is None or row_index is None:
            raise ValueError(f"Задача с task_id={task_id!r} не найдена")

        updated = dict(row)
        updated["due_date"] = new_due_date.strip()
        await sheets.update_row("tasks", row_index, updated)

        assigned_to = str(row.get("assigned_to", "")).strip()
        notes = str(row.get("notes", ""))
        try:
            await _sync_google_task_deadline(assigned_to, notes, new_due_date)
        except Exception as sync_exc:
            logger.warning(
                "Google Tasks sync failed on postpone_task: task_id=%s error=%s",
                task_id,
                sync_exc,
            )

        return {"task_id": task_id, "due_date": new_due_date}
    except Exception as exc:
        logger.error(
            "postpone_task failed: task_id=%s new_due_date=%s error=%s",
            task_id,
            new_due_date,
            exc,
            exc_info=True,
        )
        raise


async def create_task(
    title: str,
    assigned_to: str,
    due_date: str,
    notes: str,
) -> dict[str, Any]:
    """
    Создать новую задачу в таблице tasks.

    due_date в формате YYYY-MM-DD.
    assigned_to — имя, @username или должность (шеф, су-шеф) как в employees.
    Дублировать в Google Tasks (OAuth): при отсутствии списка — найти по имени или создать новый.
    """
    try:
        _, _, resolved = await _resolve_employee_row(assigned_to)
        if not resolved.ok:
            out: dict[str, Any] = {"error": resolved.error}
            if resolved.candidates:
                out["candidates"] = resolved.candidates
            return out
        assignee = resolved.canonical_name

        task_id = str(uuid.uuid4())
        row_notes = notes or ""

        tasklist_id = await _get_employee_tasklist_id(assignee, auto_create=True)
        google_synced = False
        if tasklist_id:
            try:
                google_task_id = await google_tasks.create_task(
                    tasklist_id,
                    title,
                    _due_date_to_rfc3339(due_date),
                    row_notes,
                )
                row_notes = _append_google_task_id(row_notes, google_task_id)
                google_synced = True
            except Exception as sync_exc:
                logger.warning(
                    "Google Tasks sync failed on create_task: assigned_to=%s error=%s",
                    assignee,
                    sync_exc,
                )
        elif settings.google_tasks_use_oauth:
            logger.info(
                "Google Tasks: could not resolve or create list for %r (OAuth?)",
                assignee,
            )

        await sheets.append_row(
            "tasks",
            {
                "task_id": task_id,
                "title": title,
                "assigned_to": assignee,
                "due_date": due_date,
                "status": "pending",
                "reminder_count": "0",
                "notes": row_notes,
            },
        )
        return {
            "task_id": task_id,
            "title": title,
            "assigned_to": assignee,
            "resolved_from": assigned_to.strip(),
            "matched_by": resolved.matched_by,
            "due_date": due_date,
            "status": "pending",
            "google_tasks_synced": google_synced,
        }
    except Exception as exc:
        logger.error(
            "create_task failed: title=%s assigned_to=%s error=%s",
            title,
            assigned_to,
            exc,
            exc_info=True,
        )
        raise


async def create_event(
    title: str,
    date: str,
    time: str,
    description: str,
) -> dict[str, Any]:
    """
    Создать мероприятие в Google Calendar (календарь «Мероприятия») и записать в лист events.

    date: YYYY-MM-DD, time: HH:MM.
    Используй для встреч, мероприятий, праздников, событий ресторана.
    """
    try:
        write_calendar_id = events_calendar_id()
        event_id = await google_calendar.create_event(
            write_calendar_id,
            title,
            date,
            time,
            description,
        )

        await sheets.append_row(
            "events",
            {
                "title": title,
                "date": date,
                "time": time,
                "description": description,
                "calendar_id": event_id,
                "created_by": "bot",
            },
        )
        return {
            "title": title,
            "date": date,
            "time": time,
            "calendar_event_id": event_id,
            "google_calendar_id": write_calendar_id,
        }
    except Exception as exc:
        logger.error(
            "create_event failed: title=%s date=%s time=%s error=%s",
            title,
            date,
            time,
            exc,
            exc_info=True,
        )
        raise


async def get_events_for_dates(
    preset: str = "today",
    dates: list[str] | None = None,
) -> dict[str, Any]:
    """
    Мероприятия из Google Calendar (основной + «Мероприятия») и листа events
    только за указанные дни.

    preset: today | tomorrow | yesterday | none — как в get_schedule_for_dates.
    dates: дополнительные YYYY-MM-DD.

    Используй для **мероприятий, встреч, событий** календаря (не Google Tasks).
    Для «какие задачи» / поручений в Gmail Tasks — get_tasks_for_dates.
    Для смен сотрудников — get_schedule_for_dates.
    """
    try:
        targets = sorted(_collect_schedule_target_dates(preset, dates))
        read_targets = calendars_for_read()
        calendar_events = await google_calendar.get_events_for_dates_from_calendars(
            read_targets,
            targets,
        )

        sheet_events = await sheets.read_sheet("events")
        sheet_for_dates = [
            row
            for row in sheet_events
            if _normalize_schedule_date_cell(row.get("date")) in set(targets)
        ]

        return {
            "dates": targets,
            "calendars_queried": [
                {"id": cid, "label": label} for cid, label in read_targets
            ],
            "calendar": calendar_events,
            "sheet": sheet_for_dates,
        }
    except Exception as exc:
        logger.error(
            "get_events_for_dates failed: preset=%s dates=%s error=%s",
            preset,
            dates,
            exc,
            exc_info=True,
        )
        raise


async def get_today_events() -> dict[str, Any]:
    """
    Мероприятия на сегодня (сокращение). Предпочитай get_events_for_dates(preset=today).
    """
    return await get_events_for_dates(preset="today")


async def save_automation(
    trigger_type: str,
    trigger_time: str,
    trigger_day: str,
    action: str,
    params: str,
) -> dict[str, Any]:
    """
    Сохранить автоматизацию в лист automations.

    trigger_type: daily, weekly, once и т.п.
    trigger_time: HH:MM (московское время).
    trigger_day: день недели или дата для разовых триггеров.
    action: что выполнить (send_message, remind_tasks и т.п.).
    params: JSON-строка с параметрами действия.
    Используй, когда пользователь просит настроить напоминание,
    рассылку по расписанию или повторяющееся действие.
    """
    try:
        automation_id = str(uuid.uuid4())
        await sheets.append_row(
            "automations",
            {
                "id": automation_id,
                "trigger_type": trigger_type,
                "trigger_time": trigger_time,
                "trigger_day": trigger_day,
                "action": action,
                "params": params,
                "active": "true",
            },
        )
        return {"id": automation_id, "active": True}
    except Exception as exc:
        logger.error(
            "save_automation failed: action=%s error=%s",
            action,
            exc,
            exc_info=True,
        )
        raise


async def search_knowledge(query: str, top_k: int | None = None) -> dict[str, Any]:
    """
    Семантический поиск по проиндексированной базе знаний (папка Drive).

    Используй для вопросов про меню, регламенты, инструкции, стандарты —
    до ответа по памяти или выдумывания. Возвращает фрагменты текста и названия файлов.
    """
    from src.agent.knowledge.search import search_knowledge as _search

    try:
        return await _search(query, top_k=top_k)
    except Exception as exc:
        logger.error(
            "search_knowledge failed: query=%s error=%s",
            query[:80],
            exc,
            exc_info=True,
        )
        raise


async def sync_knowledge_folder() -> dict[str, Any]:
    """
    Принудительно переиндексировать папку DRIVE_KNOWLEDGE_FOLDER_ID.

    Вызывай, когда клиент просит обновить базу знаний или после загрузки новых файлов.
    """
    from src.agent.knowledge import sync_drive_knowledge_folder

    try:
        return await sync_drive_knowledge_folder()
    except Exception as exc:
        logger.error("sync_knowledge_folder failed: %s", exc, exc_info=True)
        raise


async def list_knowledge_sources() -> dict[str, Any]:
    """
    Список проиндексированных источников (лист knowledge_sources): название, статус, ошибки.
    """
    try:
        rows = await sheets.read_sheet("knowledge_sources")
        items = [
            {
                "source_id": str(r.get("source_id", "")).strip(),
                "title": str(r.get("title", "")).strip(),
                "active": str(r.get("active", "")).strip().lower() in {"true", "1", "yes"},
                "chunk_count": str(r.get("chunk_count", "")).strip(),
                "indexed_at": str(r.get("indexed_at", "")).strip(),
                "error": str(r.get("error", "")).strip(),
            }
            for r in rows
            if str(r.get("source_id", "")).strip()
        ]
        return {"count": len(items), "sources": items}
    except Exception as exc:
        logger.error("list_knowledge_sources failed: %s", exc, exc_info=True)
        raise


async def read_drive_document(file_id: str) -> str:
    """
    Прочитать Google Doc с Drive как обычный текст.

    Используй, когда пользователь просит прочитать, пересказать
    или ответить по содержимому документа по file_id.
    """
    try:
        return await google_drive.read_document(file_id)
    except Exception as exc:
        logger.error(
            "read_drive_document failed: file_id=%s error=%s",
            file_id,
            exc,
            exc_info=True,
        )
        raise


async def register_employee(
    name: str,
    telegram_user_id: int,
    username: str,
    role: str,
) -> dict[str, Any]:
    """
    Зарегистрировать или обновить сотрудника в справочнике employees.

    telegram_user_id — числовой ID Telegram (не @username).
    Если строка с таким ID или таким именем (без учёта регистра) уже есть — обновит её,
    иначе добавит новую. Используй, когда добавляют человека в команду или привязывают Telegram.
    """
    try:
        tid = str(int(telegram_user_id))
        un = (username or "").strip()
        role_clean = (role or "").strip()
        name_clean = name.strip()

        idx, row = await _employee_row_by_telegram_id(tid)
        if row is not None and idx is not None:
            merged = dict(row)
            merged["name"] = name_clean
            merged["telegram_user_id"] = tid
            merged["username"] = un
            if role_clean:
                merged["role"] = role_clean
            await sheets.update_row("employees", idx, merged)
            return {
                "name": name_clean,
                "telegram_user_id": int(tid),
                "username": un,
                "role": str(merged.get("role", "")).strip(),
                "active": str(merged.get("active", "true")).lower() == "true",
                "updated": True,
            }

        idx2, row2 = await _employee_row_by_name_insensitive(name_clean)
        if row2 is not None and idx2 is not None:
            merged = dict(row2)
            merged["name"] = name_clean
            merged["telegram_user_id"] = tid
            merged["username"] = un
            if role_clean:
                merged["role"] = role_clean
            await sheets.update_row("employees", idx2, merged)
            return {
                "name": name_clean,
                "telegram_user_id": int(tid),
                "username": un,
                "role": str(merged.get("role", "")).strip(),
                "active": str(merged.get("active", "true")).lower() == "true",
                "updated": True,
            }

        await sheets.append_row(
            "employees",
            {
                "name": name_clean,
                "telegram_user_id": tid,
                "username": un,
                "role": role_clean,
                "google_tasks_id": "",
                "active": "true",
            },
        )
        return {
            "name": name_clean,
            "telegram_user_id": int(tid),
            "username": un,
            "role": role_clean,
            "active": True,
            "updated": False,
        }
    except Exception as exc:
        logger.error(
            "register_employee failed: name=%s telegram_user_id=%s error=%s",
            name,
            telegram_user_id,
            exc,
            exc_info=True,
        )
        raise


async def send_dm_to_employee(employee_name: str, message_text: str) -> dict[str, Any]:
    """
    Отправить сотруднику сообщение в личку (Telegram).

    В листе employees у человека должен быть заполнен telegram_user_id
    (после /start в чате с ботом или после register_employee).
    employee_name — имя (Гири), @username (Girl5719) или должность (су-шеф, шеф…)
    из колонки role в employees.
    message_text — текст для сотрудника.
    """
    try:
        row_idx, row, resolved = await _resolve_employee_row(employee_name)
        if row is None or row_idx is None:
            out: dict[str, Any] = {
                "ok": False,
                "error": resolved.error or f"Сотрудник {employee_name!r} не найден",
            }
            if resolved.candidates:
                out["candidates"] = resolved.candidates
            return out

        tid_raw = str(row.get("telegram_user_id", "")).strip()
        if not tid_raw:
            return {
                "ok": False,
                "error": (
                    "У сотрудника нет telegram_user_id. Пусть он откроет бота и нажмёт /start "
                    "(или в таблице уже указан его @username без ID — тогда после сохранения "
                    "username сотрудник снова нажимает /start)."
                ),
            }

        try:
            chat_id = int(tid_raw)
        except ValueError:
            return {"ok": False, "error": "telegram_user_id в таблице не число"}

        text = (message_text or "").strip()
        if not text:
            return {"ok": False, "error": "Пустое сообщение"}

        bot = _require_bot()
        try:
            await bot.send_message(chat_id, text[:3500])
        except TelegramForbiddenError:
            return {
                "ok": False,
                "error": (
                    "Telegram не доставил сообщение: человек не нажимал /start в этом боте "
                    "или заблокировал бота."
                ),
            }
        except TelegramBadRequest as exc:
            logger.warning("send_dm_to_employee bad request: %s", exc)
            return {"ok": False, "error": str(exc)}

        return {
            "ok": True,
            "employee_name": str(row.get("name", "")).strip(),
            "resolved_from": employee_name.strip(),
            "matched_by": resolved.matched_by,
            "telegram_user_id": chat_id,
            "sent_characters": min(len(text), 3500),
        }
    except Exception as exc:
        logger.error(
            "send_dm_to_employee failed: employee_name=%s error=%s",
            employee_name,
            exc,
            exc_info=True,
        )
        raise


async def send_brief_to_primary_work_chat(message_text: str) -> dict[str, Any]:
    """
    Отправить короткое сообщение в основной рабочий чат (первая активная строка листа chats).

    Для срочного уведомления команде (до ~450 символов). Лимит частоты: env
    GROUP_NOTICE_COOLDOWN_SEC (по умолчанию 3600; 0 = без лимита); memory_facts
    employee=_system_group_notice_cooldown.
    """
    try:
        text = (message_text or "").strip()
        if not text:
            return {"ok": False, "error": "Пустое сообщение"}
        if len(text) > 450:
            text = text[:447] + "..."

        rows_m = await sheets.read_sheet("memory_facts")
        last_ts: float | None = None
        for row in rows_m:
            if str(row.get("employee", "")).strip() != _GROUP_NOTICE_COOLDOWN_EMPLOYEE:
                continue
            raw = str(row.get("fact", "")).strip()
            try:
                last_ts = float(raw)
            except ValueError:
                last_ts = None
            break

        now_ts = time.time()
        cooldown = int(settings.group_notice_cooldown_sec)
        if cooldown > 0 and last_ts is not None and now_ts - last_ts < cooldown:
            wait = int(cooldown - (now_ts - last_ts))
            return {
                "ok": False,
                "error": "Лимит: в общий чат не чаще одного сообщения в час.",
                "retry_after_seconds": max(wait, 1),
            }

        chats = await sheets.read_sheet("chats")
        chat_id: int | None = None
        for row in chats:
            if str(row.get("active", "")).strip().lower() not in {
                "true",
                "1",
                "yes",
                "on",
            }:
                continue
            raw = str(row.get("chat_id", "")).strip()
            if not raw:
                continue
            try:
                chat_id = int(raw)
                break
            except ValueError:
                continue

        if chat_id is None:
            return {"ok": False, "error": "Нет активного чата в листе chats."}

        bot = _require_bot()
        try:
            await bot.send_message(chat_id, text[:3500])
        except TelegramForbiddenError:
            return {
                "ok": False,
                "error": "Нет доступа к чату (бот удалён из группы или нет прав).",
            }
        except TelegramBadRequest as exc:
            logger.warning("send_brief_to_primary_work_chat bad request: %s", exc)
            return {"ok": False, "error": str(exc)}

        await sheets.upsert_memory_fact_row(
            _GROUP_NOTICE_COOLDOWN_EMPLOYEE,
            str(now_ts),
        )
        return {
            "ok": True,
            "chat_id": chat_id,
            "sent_characters": len(text),
        }
    except Exception as exc:
        logger.error(
            "send_brief_to_primary_work_chat failed: error=%s",
            exc,
            exc_info=True,
        )
        raise


async def delegate_private_reminder(
    employee_name: str,
    title: str,
    message_to_employee: str,
    due_date: str,
    notes_for_task: str = "",
    checklist_items: list[str] | None = None,
) -> dict[str, Any]:
    """
    Поручение сотруднику в личку + запись в лист tasks (фаза «умного посредника»).

    Создаёт задачу и отправляет сотруднику текст в Telegram. В конце сообщения добавляется
    ID задачи — по нему в будущем можно привязывать ответы (реплай).
    employee_name — имя, @username или должность (су-шеф, шеф…) — см. employees.role.
    title — короткое название задачи для таблицы.
    message_to_employee — полный текст, который увидит человек в личке.
    due_date — YYYY-MM-DD (срок / день контроля).
    notes_for_task — доп. текст в задачу (скрытые детали); если пусто — в notes попадёт смысл поручения.
    checklist_items — пункты чеклиста: в notes добавится блок __DELEGATION_JSON__ с полем checklist.
    """
    try:
        row_notes = _build_delegate_task_notes(
            message_to_employee,
            notes_for_task,
            checklist_items,
        )

        task_result = await create_task(
            title.strip(),
            employee_name.strip(),
            due_date.strip(),
            row_notes,
        )
        if task_result.get("error"):
            return {
                "ok": False,
                "error": task_result.get("error"),
                "candidates": task_result.get("candidates"),
            }
        task_id = str(task_result.get("task_id", "")).strip()
        assignee = str(task_result.get("assigned_to", employee_name)).strip()
        body = (message_to_employee or "").strip()
        if not body:
            return {
                "ok": False,
                "task": task_result,
                "dm": {"ok": False, "error": "Пустое message_to_employee"},
            }

        footer = (
            f"\n\n────────\n"
            f"ID задачи: `{task_id}`\n"
            f"Когда выполнишь — ответь **на это сообщение** фото или текстом.\n"
            f"Если отвечаешь не реплаем и открыто несколько поручений — "
            f"укажи этот ID в тексте сообщения."
        )
        dm_text = (body + footer)[:3500]
        dm_result = await send_dm_to_employee(assignee, dm_text)

        if task_id and _delegation_checklist_from_notes(row_notes):
            try:
                await _set_task_status(task_id, TASK_STATUS_AWAITING_PROOF)
                task_result = {**task_result, "status": TASK_STATUS_AWAITING_PROOF}
            except Exception as st_exc:
                logger.warning(
                    "delegate_private_reminder: awaiting_proof status failed task_id=%s %s",
                    task_id,
                    st_exc,
                )

        out: dict[str, Any] = {
            "ok": bool(dm_result.get("ok")),
            "task": task_result,
            "dm": dm_result,
        }
        if not dm_result.get("ok"):
            out["warning"] = (
                "Задача создана, но личное сообщение не доставлено. "
                "Проверьте /start у сотрудника и telegram_user_id в employees."
            )
        return out
    except Exception as exc:
        logger.error(
            "delegate_private_reminder failed: employee=%s error=%s",
            employee_name,
            exc,
            exc_info=True,
        )
        raise


async def save_fact(fact: str, employee: str | None = None) -> dict[str, Any]:
    """
    Сохранить важный факт в долгосрочную память (лист memory_facts).

    Вызывай, когда пользователь говорит «запомни», «не забудь», «важно знать»
    или описывает постоянное правило.
    fact: суть факта одним предложением.
    employee: имя сотрудника, если факт про конкретного человека.
    """
    try:
        await sheets.append_row(
            "memory_facts",
            {
                "employee": employee or "",
                "fact": fact,
                "created_at": _now_local_str(),
            },
        )
        return {"fact": fact, "employee": employee or ""}
    except Exception as exc:
        logger.error(
            "save_fact failed: fact=%s employee=%s error=%s",
            fact,
            employee,
            exc,
            exc_info=True,
        )
        raise
