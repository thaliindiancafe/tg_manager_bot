"""Due-date reminders for open tasks in Google Tasks (OAuth lists per employee)."""

from __future__ import annotations

import logging
import re
from datetime import datetime

from aiogram import Bot

from src.agent import tools as agent_tools
from src.config import settings
from src.google import sheets
from src.google import tasks as google_tasks
from src.google.tasklist_resolve import resolve_tasklist_for_employee_row

logger = logging.getLogger(__name__)

_GT_REMINDER_PREFIX = "__GT_REMINDER__:"
_GT_REMINDER_RE = re.compile(r"__GT_REMINDER__:(\d{4}-\d{2}-\d{2})")


def _reminder_marker(today_iso: str) -> str:
    return f"{_GT_REMINDER_PREFIX}{today_iso}"


def _sent_reminder_today(notes: str, today_iso: str) -> bool:
    return _reminder_marker(today_iso) in (notes or "")


def _append_marker(notes: str, marker: str) -> str:
    base = (notes or "").strip()
    if marker in base:
        return base
    return f"{base}\n{marker}".strip() if base else marker


async def _patch_task_notes(
    tasklist_id: str,
    task_id: str,
    new_notes: str,
) -> None:
    await google_tasks.patch_task_notes(tasklist_id, task_id, new_notes)


async def run_google_tasks_due_reminders(bot: Bot, now: datetime) -> int:
    """
    Open Google Tasks with due_date <= today: one DM per task per calendar day.

    Requires telegram_user_id in employees and OAuth task list (or manager «Мои задачи»).
    """
    if not settings.google_tasks_reminders_enabled:
        return 0
    if not settings.google_tasks_use_oauth:
        return 0

    today = now.date()
    today_iso = today.isoformat()
    sent = 0

    try:
        employees = await sheets.read_sheet("employees")
    except Exception as exc:
        logger.error("google_tasks_reminders: read employees failed: %s", exc, exc_info=True)
        return 0

    for row in employees:
        if str(row.get("active", "true")).strip().lower() in {"false", "0", "no", "off"}:
            continue

        tid_raw = str(row.get("telegram_user_id", "")).strip()
        if not tid_raw:
            continue

        name = str(row.get("name", "")).strip()
        if not name:
            continue

        resolved = await resolve_tasklist_for_employee_row(row)
        if not resolved:
            continue

        tasklist_id, list_title = resolved

        try:
            dated, _ = await google_tasks.list_all_open_tasks_in_tasklist(
                tasklist_id,
                list_title,
            )
        except Exception as exc:
            logger.warning(
                "google_tasks_reminders: list failed employee=%s error=%s",
                name,
                exc,
            )
            continue

        for task in dated:
            due_s = str(task.get("due_date", "")).strip()[:10]
            if not due_s:
                continue
            try:
                due_d = datetime.strptime(due_s, "%Y-%m-%d").date()
            except ValueError:
                continue
            if due_d > today:
                continue

            task_id = str(task.get("id", "")).strip()
            title = str(task.get("title", "")).strip() or "(без названия)"
            notes = str(task.get("notes", ""))

            if _sent_reminder_today(notes, today_iso):
                continue

            overdue = due_d < today
            overdue_note = " (срок уже прошёл)" if overdue else ""
            body = (
                f"Напоминание по задаче Google Tasks{overdue_note}:\n"
                f"{title}\n"
                f"Срок: {due_s}\n"
                f"Список: {list_title}"
            )

            dm = await agent_tools.send_dm_to_employee(name, body[:3400])
            if not dm.get("ok"):
                logger.warning(
                    "google_tasks_reminders: DM failed employee=%s task=%s error=%s",
                    name,
                    task_id,
                    dm.get("error"),
                )
                continue

            if task_id:
                try:
                    await _patch_task_notes(
                        tasklist_id,
                        task_id,
                        _append_marker(notes, _reminder_marker(today_iso)),
                    )
                except Exception as patch_exc:
                    logger.warning(
                        "google_tasks_reminders: notes patch failed task=%s %s",
                        task_id,
                        patch_exc,
                    )

            sent += 1
            logger.info(
                "google_tasks_reminders: sent employee=%s task=%s due=%s",
                name,
                task_id,
                due_s,
            )

    if sent:
        logger.info("google_tasks_reminders: finished sent=%s date=%s", sent, today_iso)
    return sent
