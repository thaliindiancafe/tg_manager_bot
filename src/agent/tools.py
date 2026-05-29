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
from src.google import tasks as google_tasks
from src.google.oauth_credentials import oauth_configured
from src.storage import get_store

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


def _use_db_store() -> bool:
    return (getattr(settings, "storage_backend", "sheets") or "sheets").strip().lower() == "db"


async def _employees_rows() -> list[dict[str, Any]]:
    if _use_db_store():
        return await get_store().employees.list_employees()
    from src.google import sheets  # local import to avoid accidental db usage

    return await sheets.read_sheet("employees")


async def _tasks_rows() -> list[dict[str, Any]]:
    if _use_db_store():
        return await get_store().tasks.list_tasks()
    from src.google import sheets  # local import

    return await sheets.read_sheet("tasks")


async def _schedule_rows() -> list[dict[str, Any]]:
    if _use_db_store():
        return await get_store().schedule.list_schedule()
    from src.google import sheets  # local import

    return await sheets.read_sheet("schedule")


async def _employee_row_by_telegram_id(
    telegram_user_id: str,
) -> tuple[int | None, dict[str, Any] | None]:
    tid = str(telegram_user_id).strip()
    rows = await _employees_rows()
    for offset, row in enumerate(rows):
        if str(row.get("telegram_user_id", "")).strip() == tid:
            return (offset + 2) if not _use_db_store() else None, row
    return None, None


async def _resolve_employee_row(
    query: str,
) -> tuple[int | None, dict[str, Any] | None, EmployeeResolveResult]:
    q = query.strip()
    if not q:
        return None, None, EmployeeResolveResult(
            ok=False, error="Пустой идентификатор сотрудника"
        )
    rows = await _employees_rows()

    if is_shift_unpacking_query(q):
        result = await resolve_shift_unpacking_from_schedule(rows)
    else:
        result = resolve_employee_reference(q, rows)

    if not result.ok:
        return None, None, result
    target = result.canonical_name.strip().lower()
    for offset, row in enumerate(rows):
        if str(row.get("name", "")).strip().lower() == target:
            return (offset + 2) if not _use_db_store() else None, row, result
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
        rows = await _employees_rows()
        body = format_staff_directory(rows)
        return (
            "## Справочник сотрудников (employees)\n"
            f"{body}\n\n"
            "Для **send_dm_to_employee**, **delegate_private_reminder**, **create_task** "
            "в поле имени можно передать: **имя** (Гири), **@username** (Girl5719) "
            "или **должность** (су-шеф, шеф, бариста…). "
            "Должности — поле **role** в справочнике сотрудников. "
            "**Помощник на смене** / разбор товара — кто сегодня в графике (schedule, блок "
            f"{getattr(settings, 'schedule_unpacking_roles', 'Kleener')}), не фиксированное имя."
        )
    except Exception as exc:
        logger.warning("build_staff_roles_section failed: %s", exc)
        return ""


async def get_employee_directory() -> dict[str, Any]:
    """
    Список активных сотрудников: имя, должность (role), username, есть ли Telegram.

    Вызывай, если нужно понять, кому поручение (шеф, су-шеф) или кого нет в справочнике.
    """
    try:
        rows = await _employees_rows()
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
                "role в справочнике: Гири→су-шеф, Пракаш→шеф. "
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
    rows = await _employees_rows()
    out: list[tuple[int | None, dict[str, Any]]] = []
    for offset, row in enumerate(rows):
        cell = _normalize_username(str(row.get("username", "")))
        if cell == u:
            sheet_idx = (offset + 2) if not _use_db_store() else None
            out.append((sheet_idx, row))
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
    if row is None:
        raise ValueError(f"Задача с task_id={task_id!r} не найдена")
    if _use_db_store():
        await get_store().tasks.update_task_fields(task_id, {"status": status})
        return
    if row_index is None:
        raise ValueError(f"Задача с task_id={task_id!r} не найдена")
    updated = dict(row)
    updated["status"] = status
    from src.google import sheets

    await sheets.update_row("tasks", row_index, updated)


async def set_pending_proof_task(telegram_user_id: int, task_id: str) -> None:
    key = f"{PENDING_PROOF_FACT_PREFIX}{int(telegram_user_id)}"
    if _use_db_store():
        await get_store().memory.upsert_fact(employee=key, fact=str(task_id).strip())
        return
    from src.google import sheets

    await sheets.upsert_memory_fact_row(key, str(task_id).strip())


async def pop_pending_proof_task(telegram_user_id: int) -> str | None:
    key = f"{PENDING_PROOF_FACT_PREFIX}{int(telegram_user_id)}"
    from src.google.sheets import get_facts

    facts = await get_facts()
    for row in facts:
        if str(row.get("employee", "")).strip() == key:
            tid = str(row.get("fact", "")).strip()
            if tid:
                from src.google.sheets import upsert_memory_fact_row

                await upsert_memory_fact_row(key, "")
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
    if _use_db_store():
        if sheet_name == "employees":
            rows = await _employees_rows()
        elif sheet_name == "tasks":
            rows = await _tasks_rows()
        elif sheet_name == "schedule":
            rows = await _schedule_rows()
        else:
            # Non-migrated sheets remain in Google Sheets during transition
            from src.google import sheets

            rows = await sheets.read_sheet(sheet_name)
    else:
        from src.google import sheets

        rows = await sheets.read_sheet(sheet_name)
    target = str(value).strip()
    for offset, row in enumerate(rows):
        if str(row.get(column, "")).strip() == target:
            return (offset + 2) if not _use_db_store() else None, row
    return None, None


async def _get_employee_tasklist_id(
    employee_name: str,
    *,
    auto_create: bool = True,
) -> str | None:
    row_idx, row = await _employee_row_by_name_insensitive(employee_name)
    if not row:
        return None
    # DB backend cannot rely on Sheets row index updates; implement a local resolver.
    existing = str(row.get("google_tasks_id", "")).strip()
    if existing:
        return existing

    if not (auto_create and settings.google_tasks_use_oauth and oauth_configured()):
        return None

    # Try find tasklist by employee name, else create.
    try:
        lists = await google_tasks.list_tasklists()
        target = employee_name.strip().lower()
        matched = next(
            (tl for tl in lists if str(tl.get("title", "")).strip().lower() == target),
            None,
        )
        if matched and str(matched.get("id", "")).strip():
            tasklist_id = str(matched["id"]).strip()
        else:
            created = await google_tasks.create_tasklist(employee_name.strip())
            tasklist_id = str(created.get("id", "")).strip()
        if tasklist_id:
            if _use_db_store():
                await get_store().employees.upsert_employee(
                    name=str(row.get("name", "")).strip() or employee_name.strip(),
                    telegram_user_id=str(row.get("telegram_user_id", "")).strip(),
                    username=str(row.get("username", "")).strip(),
                    role=str(row.get("role", "")).strip(),
                    google_tasks_id=tasklist_id,
                    active=str(row.get("active", "true")).strip(),
                )
            else:
                from src.google.tasklist_resolve import ensure_tasklist_for_employee_row

                if row_idx is not None:
                    await ensure_tasklist_for_employee_row(row_idx, row)
            return tasklist_id
    except Exception as exc:
        logger.warning("Could not ensure tasklist for %s: %s", employee_name, exc)
        return None

    return None


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
    Смены из schedule (Supabase при STORAGE_BACKEND=db) за указанные календарные дни.

    preset: today | tomorrow | yesterday | none — относительный день в часовом поясе приложения.
    dates: дополнительные даты в формате YYYY-MM-DD (например конкретный день недели).

    Используй для «кто сегодня на смене» → preset=today;
    «кто завтра» → preset=tomorrow; «кто вчера» → preset=yesterday;
    за неделю — preset=none и несколько дат в dates. Не смешивай дни: возвращай только запрошенные.
    """
    try:
        targets = _collect_schedule_target_dates(preset, dates)
        schedule = await _schedule_rows()
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
    Задачи на указанные дни: Google Tasks API (OAuth) + метаданные бота в БД/Sheets.

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

        bot_rows: list[dict[str, Any]] = []
        for row in await _tasks_rows():
            due = _normalize_schedule_date_cell(row.get("due_date"))
            if due not in target_set:
                continue
            if is_closed_status(str(row.get("status", "")).strip()):
                continue
            bot_rows.append(row)

        return {
            "dates": targets,
            "google_tasks": google_tasks_rows,
            "google_tasks_error": google_error,
            "bot_tasks": bot_rows,
            "sheets_tasks": bot_rows,
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
        for row in await _tasks_rows():
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
        for row in await _tasks_rows():
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
        tasks = await _tasks_rows()
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
        tasks = await _tasks_rows()
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
        if _use_db_store():
            await get_store().tasks.update_task_fields(task_id, {"status": TASK_STATUS_DONE})
        else:
            from src.google import sheets

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
    if _use_db_store():
        await get_store().tasks.update_task_fields(
            task_id, {"notes": notes, "status": new_status}
        )
    else:
        from src.google import sheets

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
        if _use_db_store():
            await get_store().tasks.update_task_fields(task_id.strip(), {"status": TASK_STATUS_DONE})
        else:
            from src.google import sheets

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
        if _use_db_store():
            await get_store().tasks.update_task_fields(
                task_id.strip(),
                {"notes": notes[:12000], "status": TASK_STATUS_AWAITING_PROOF},
            )
        else:
            from src.google import sheets

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
        if _use_db_store():
            await get_store().tasks.update_task_fields(
                task_id, {"due_date": new_due_date.strip()}
            )
        else:
            from src.google import sheets

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
    Создать задачу в Google Tasks (список исполнителя) и сохранить метаданные у бота.

    due_date в формате YYYY-MM-DD.
    assigned_to — имя, @username или должность (шеф, су-шеф) как в справочнике сотрудников.
    В ответе есть verification_url — ссылку нужно отправить пользователю для проверки.
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
        google_task_id = ""
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

        task_row: dict[str, Any] = {
            "task_id": task_id,
            "title": title,
            "assigned_to": assignee,
            "due_date": due_date,
            "status": "pending",
            "reminder_count": "0",
            "notes": row_notes,
            "google_task_id": google_task_id,
            "google_tasklist_id": tasklist_id or "",
        }
        if _use_db_store():
            await get_store().tasks.upsert_task(task_row)
        else:
            from src.google import sheets

            await sheets.append_row("tasks", task_row)

        from src.utils.google_links import google_task_url

        verification_url = ""
        if google_synced and google_task_id and tasklist_id:
            verification_url = google_task_url(
                tasklist_id=tasklist_id,
                task_id=google_task_id,
            )
        elif tasklist_id:
            verification_url = google_task_url(tasklist_id=tasklist_id)

        return {
            "task_id": task_id,
            "title": title,
            "assigned_to": assignee,
            "resolved_from": assigned_to.strip(),
            "matched_by": resolved.matched_by,
            "due_date": due_date,
            "status": "pending",
            "google_tasks_synced": google_synced,
            "verification_url": verification_url,
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
    Создать мероприятие в Google Calendar (календарь «Мероприятия»).

    Обязательно вызывай, когда просят создать/запланировать мероприятие, встречу, событие.
    date: YYYY-MM-DD, time: HH:MM (если время не сказано — разумное по смыслу).
    В ответе verification_url — ссылку на событие нужно отправить пользователю.
    """
    try:
        write_calendar_id = events_calendar_id()
        created = await google_calendar.create_event(
            write_calendar_id,
            title,
            date,
            time,
            description,
        )
        event_id = str(created.get("event_id", "")).strip()
        html_link = str(created.get("html_link", "")).strip()

        if not _use_db_store() and not getattr(settings, "calendar_only_mode", False):
            from src.google import sheets

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

        from src.utils.google_links import pick_calendar_event_url

        verification_url = pick_calendar_event_url(
            calendar_id=write_calendar_id,
            event_id=event_id,
            html_link=html_link,
        )
        return {
            "title": title,
            "date": date,
            "time": time,
            "calendar_event_id": event_id,
            "google_calendar_id": write_calendar_id,
            "verification_url": verification_url,
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
    Мероприятия напрямую из Google Calendar API (основной + «Мероприятия»)
    за указанные дни. Лист events в Sheets не используется при STORAGE_BACKEND=db.

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

        sheet_for_dates: list[dict[str, Any]] = []
        if not _use_db_store() and not getattr(settings, "calendar_only_mode", False):
            from src.google import sheets

            sheet_events = await sheets.read_sheet("events")
            sheet_for_dates = [
                row
                for row in sheet_events
                if _normalize_schedule_date_cell(row.get("date")) in set(targets)
            ]

        sheet_event_ids = {
            str(row.get("calendar_id", "")).strip()
            for row in sheet_for_dates
            if str(row.get("calendar_id", "")).strip()
        }
        calendar_live = [
            ev
            for ev in calendar_events
            if str(ev.get("id", "")).strip() not in sheet_event_ids
        ]

        return {
            "dates": targets,
            "calendars_queried": [
                {"id": cid, "label": label} for cid, label in read_targets
            ],
            "events": calendar_live,
            "calendar_live": calendar_live,
            "sheet": sheet_for_dates,
            "note": (
                "events/calendar_live — только Google Calendar API; "
                "sheet пуст при STORAGE_BACKEND=db / CALENDAR_ONLY_MODE"
            ),
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


async def extract_tasks_from_chat(
    chat_id: int,
    since_days: int = 7,
    limit_messages: int = 1500,
) -> dict[str, Any]:
    """
    Mira-like helper: scan group chat transcript (last N days) and propose tasks.

    This tool only works when STORAGE_BACKEND=db (messages are logged into chat_messages).
    Returns a list of proposed tasks with minimal structure:
    - title
    - assigned_to (best-effort, from @username or name mention)
    - evidence (message_id + short quote)
    """
    if not _use_db_store():
        return {
            "ok": False,
            "error": "Чат-лог доступен только при STORAGE_BACKEND=db (нужно включить БД).",
            "tasks": [],
        }
    try:
        from datetime import datetime, timedelta
        from zoneinfo import ZoneInfo

        tz = ZoneInfo(settings.timezone)
        since_dt = datetime.now(tz) - timedelta(days=max(1, int(since_days)))
        msgs = await get_store().chat_messages.list_chat_messages_since(
            chat_id=int(chat_id),
            since_dt=since_dt,
            limit=int(limit_messages),
        )
        if not msgs:
            return {"ok": True, "chat_id": int(chat_id), "since_days": since_days, "tasks": []}

        from src.bot.employee_tasks_query import _find_employee_in_text
        from src.utils.employee_name_match import build_name_lookup
        from src.utils.task_due_hint import parse_due_date_hint

        employees = await _employees_rows()
        employee_names = [
            str(e.get("name", "")).strip() for e in employees if str(e.get("name", "")).strip()
        ]
        by_username = {
            _normalize_username(str(e.get("username", ""))): str(e.get("name", "")).strip()
            for e in employees
            if str(e.get("name", "")).strip() and str(e.get("username", "")).strip()
        }
        name_lookup = build_name_lookup(employee_names)

        verb_markers = (
            "сделай",
            "сделать",
            "нужно",
            "надо",
            "проверь",
            "проверить",
            "подготовь",
            "подготовить",
            "закажи",
            "заказать",
            "купить",
            "срочно",
            "поруч",
        )
        tasks: list[dict[str, Any]] = []
        seen_msg: set[int] = set()
        seen_title: set[str] = set()

        def _norm_title(value: str) -> str:
            t = re.sub(r"\s+", " ", (value or "").lower()).strip()
            t = re.sub(r"[^\wа-яё\s]", "", t, flags=re.IGNORECASE)
            return t[:80]

        for m in msgs:
            txt = (m.text or "").strip()
            low = txt.lower()
            if not txt or len(txt) < 6:
                continue
            if not any(v in low for v in verb_markers):
                continue
            if m.message_id in seen_msg:
                continue

            assignee = ""
            for token in re.findall(r"@[a-zA-Z][a-zA-Z0-9_]{2,}", txt):
                u = _normalize_username(token.lstrip("@"))
                assignee = by_username.get(u) or ""
                if assignee:
                    break
            if not assignee:
                found = _find_employee_in_text(txt, employee_names, lookup=name_lookup)
                assignee = found or ""
            if not assignee:
                from src.utils.employee_role_resolve import (
                    resolve_employee_reference,
                    role_to_canonical,
                )

                for token in re.findall(r"[a-zA-Zа-яА-ЯёЁ][a-zA-Zа-яА-ЯёЁ\-]{1,}", txt):
                    if not role_to_canonical(token):
                        continue
                    resolved = resolve_employee_reference(token, employees)
                    if resolved.ok and resolved.canonical_name:
                        assignee = resolved.canonical_name
                        break

            title = re.split(r"[\n\.!?]+", txt, maxsplit=1)[0].strip()
            title = re.sub(r"^@\w+\s*", "", title).strip()
            title = title[:120]
            if not title:
                continue

            title_key = _norm_title(title)
            if title_key in seen_title:
                continue
            seen_msg.add(m.message_id)
            seen_title.add(title_key)

            due_date = parse_due_date_hint(txt, tz_name=settings.timezone)
            tasks.append(
                {
                    "title": title,
                    "assigned_to": assignee,
                    "due_date": due_date,
                    "evidence": {
                        "message_id": m.message_id,
                        "quote": txt[:220],
                        "author_username": (m.username or "").strip(),
                        "author_name": (m.full_name or "").strip(),
                    },
                }
            )

        return {
            "ok": True,
            "chat_id": int(chat_id),
            "since_days": int(since_days),
            "messages_scanned": len(msgs),
            "tasks": tasks[:200],
        }
    except Exception as exc:
        logger.error("extract_tasks_from_chat failed: chat_id=%s error=%s", chat_id, exc, exc_info=True)
        return {"ok": False, "error": str(exc), "tasks": []}

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
    Список проиндексированных источников (БД или Sheets): название, статус, ошибки.
    """
    try:
        from src.storage.access import list_knowledge_sources

        rows = await list_knowledge_sources()
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


def _valid_telegram_user_id(raw: int | None) -> str | None:
    """Positive Telegram user id as string, or None if unknown / invalid."""
    if raw is None:
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    if value <= 0:
        return None
    return str(value)


def _employee_row_payload(
    name: str,
    username: str,
    role: str,
    telegram_user_id: str | None,
    *,
    existing: dict[str, Any] | None = None,
) -> dict[str, str]:
    base = dict(existing) if existing else {}
    tid = telegram_user_id or str(base.get("telegram_user_id", "")).strip()
    if tid in {"0", ""}:
        tid = telegram_user_id or ""
    payload = {
        "name": name,
        "telegram_user_id": tid,
        "username": username,
        "role": role or str(base.get("role", "")).strip(),
        "google_tasks_id": str(base.get("google_tasks_id", "")).strip(),
        "active": "true",
    }
    return payload


async def _find_employee_row_for_register(
    name: str,
    username: str,
    telegram_user_id: str | None,
) -> tuple[int | None, dict[str, Any] | None]:
    if telegram_user_id:
        idx, row = await _employee_row_by_telegram_id(telegram_user_id)
        if row is not None:
            return idx, row

    un = _normalize_username(username)
    if un:
        matches = await _employee_rows_by_username(un)
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            return None, None  # caller handles ambiguity

    return await _employee_row_by_name_insensitive(name)


async def register_employee(
    name: str,
    username: str = "",
    role: str = "",
    telegram_user_id: int = 0,
) -> dict[str, Any]:
    """
    Добавить или обновить сотрудника в справочнике.

    name — имя (Ирина, Роксана).
    username — @ник (с @ или без); желательно для привязки после /start в личке с ботом.
    role — должность (менеджер, бариста, шеф, уборщик…).
    telegram_user_id — числовой ID Telegram; передай 0 или не указывай, если ID ещё неизвестен.

    Поиск строки: telegram_user_id → @username → имя. Не выдумывай ID.
    После /start сотрудник с тем же @username получит telegram_user_id автоматически.
    """
    try:
        name_clean = (name or "").strip()
        if not name_clean:
            return {"ok": False, "error": "Пустое имя сотрудника"}

        un = _normalize_username(username)
        role_clean = (role or "").strip()
        tid = _valid_telegram_user_id(telegram_user_id)

        un_matches = await _employee_rows_by_username(un) if un else []
        if len(un_matches) > 1:
            names = [str(r.get("name", "")) for _, r in un_matches]
            return {
                "ok": False,
                "error": f"Несколько строк с @{un}: {', '.join(names)}. Уточните в справочнике.",
            }

        idx, row = await _find_employee_row_for_register(
            name_clean, un, tid
        )

        if row is not None:
            merged = _employee_row_payload(
                name_clean,
                un or str(row.get("username", "")).strip(),
                role_clean,
                tid,
                existing=row,
            )
            if _use_db_store():
                await get_store().employees.upsert_employee(
                    name=str(merged["name"]).strip(),
                    telegram_user_id=str(merged.get("telegram_user_id", "")).strip(),
                    username=str(merged.get("username", "")).strip(),
                    role=str(merged.get("role", "")).strip(),
                    google_tasks_id=str(merged.get("google_tasks_id", "")).strip(),
                    active=str(merged.get("active", "true")).strip(),
                )
            else:
                if idx is None:
                    return {"ok": False, "error": "Не найдена строка employees для обновления"}
                await sheets.update_row("employees", idx, merged)
            out_tid = str(merged.get("telegram_user_id", "")).strip()
            return {
                "ok": True,
                "name": name_clean,
                "username": merged["username"],
                "role": merged["role"],
                "telegram_user_id": int(out_tid) if out_tid.isdigit() else None,
                "active": True,
                "updated": True,
            }

        payload = _employee_row_payload(name_clean, un, role_clean, tid)
        if _use_db_store():
            await get_store().employees.upsert_employee(
                name=name_clean,
                telegram_user_id=str(payload.get("telegram_user_id", "")).strip(),
                username=str(payload.get("username", "")).strip(),
                role=str(payload.get("role", "")).strip(),
                google_tasks_id=str(payload.get("google_tasks_id", "")).strip(),
                active="true",
            )
        else:
            await sheets.append_row("employees", payload)
        return {
            "ok": True,
            "name": name_clean,
            "username": un,
            "role": role_clean,
            "telegram_user_id": int(tid) if tid else None,
            "active": True,
            "updated": False,
            "hint": (
                "Строка создана. Пусть сотрудник напишет боту /start в личке — "
                "подставится telegram_user_id по @username."
                if not tid
                else ""
            ),
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


async def register_employees_bulk(employees_text: str) -> dict[str, Any]:
    """
    Добавить или обновить нескольких сотрудников в employees из текста списка.

    Формат строк: «Имя, должность - @username» (как в сообщении руководителя).
    telegram_user_id не нужен — подтянется после /start по @username.

    Всегда вызывай этот tool для «внеси в базу», «добавь сотрудников» со списком.
    Отвечай пользователю только по полям ok_count, failed, results — не выдумывай успех.
    """
    from src.utils.employee_register_parse import parse_employees_bulk_text

    try:
        parsed = parse_employees_bulk_text(employees_text)
        if not parsed:
            return {
                "ok": False,
                "error": (
                    "Не распознан ни один сотрудник. Формат: «Имя, должность - @username» "
                    "по одному на строку."
                ),
                "ok_count": 0,
                "failed": [],
            }

        results: list[dict[str, Any]] = []
        failed: list[dict[str, Any]] = []

        for item in parsed:
            res = await register_employee(
                item.name,
                username=item.username,
                role=item.role,
                telegram_user_id=0,
            )
            entry = {
                "name": item.name,
                "username": item.username,
                "role": item.role,
                **res,
            }
            results.append(entry)
            if not res.get("ok"):
                failed.append(entry)

        ok_count = sum(1 for r in results if r.get("ok"))
        return {
            "ok": ok_count > 0 and not failed,
            "ok_count": ok_count,
            "total_parsed": len(parsed),
            "failed": failed,
            "results": results,
        }
    except Exception as exc:
        logger.error("register_employees_bulk failed: %s", exc, exc_info=True)
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

        from src.google.sheets import get_facts

        rows_m = await get_facts()
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

        from src.storage.access import list_chats

        chats = await list_chats()
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
    Поручение сотруднику в личку + задача в Google Tasks (фаза «умного посредника»).

    Создаёт задачу и отправляет сотруднику текст в Telegram. В конце сообщения добавляется
    ID задачи — по нему в будущем можно привязывать ответы (реплай).
    employee_name — имя, @username или должность (су-шеф, шеф…) — см. справочник сотрудников.
    title — короткое название задачи.
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

        verification_url = str(task_result.get("verification_url", "")).strip()
        out: dict[str, Any] = {
            "ok": bool(dm_result.get("ok")),
            "task": task_result,
            "dm": dm_result,
            "verification_url": verification_url,
        }
        if not dm_result.get("ok"):
            out["warning"] = (
                "Задача создана, но личное сообщение не доставлено. "
                "Проверьте /start у сотрудника и telegram_user_id в справочнике."
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
    Сохранить важный факт в долгосрочную память (Supabase memory_facts).

    Вызывай, когда пользователь говорит «запомни», «не забудь», «важно знать»
    или описывает постоянное правило.
    fact: суть факта одним предложением.
    employee: имя сотрудника, если факт про конкретного человека.
    """
    try:
        key = (employee or "_general").strip() or "_general"
        if _use_db_store():
            await get_store().memory.upsert_fact(employee=key, fact=fact)
        else:
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
