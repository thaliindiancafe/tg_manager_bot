"""Due-date task reminders and optional escalation (delegation roadmap phase 4)."""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

from src.agent import tools as agent_tools
from src.agent.task_status import TASK_STATUS_REVIEW, is_closed_status, normalize_status
from src.config import settings
from src.google import sheets
from src.utils.employee_name_match import match_employee_name
from src.utils.employee_role_resolve import resolve_employee_reference

logger = logging.getLogger(__name__)

_REMINDER_MARKER_PREFIX = "__TASK_REMINDER_DATE__:"
_GROUP_ESCALATION_MARKER = "__TASK_GROUP_ESCALATION__:v1"
_UNRESOLVED_ASSIGNEE_MARKER = "__TASK_REMINDER_UNRESOLVED_ASSIGNEE__:v1"
_DATE_ISO_LEN = 10


def _is_active_chat(value: Any) -> bool:
    return str(value).strip().lower() in {"true", "1", "yes", "on"}


def _reminder_marker(today_iso: str) -> str:
    return f"{_REMINDER_MARKER_PREFIX}{today_iso}"


def _sent_reminder_today(notes: str, today_iso: str) -> bool:
    return _reminder_marker(today_iso) in (notes or "")


def _escalation_sent(notes: str) -> bool:
    return _GROUP_ESCALATION_MARKER in (notes or "")


def _unresolved_assignee_marked(notes: str) -> bool:
    return _UNRESOLVED_ASSIGNEE_MARKER in (notes or "")


def _append_lines(base: str, *lines: str) -> str:
    b = (base or "").strip()
    extra = "\n".join(lines).strip()
    if not extra:
        return b
    return f"{b}\n{extra}".strip() if b else extra


def _parse_due_date(raw: Any) -> date | None:
    s = str(raw or "").strip()[:_DATE_ISO_LEN]
    if len(s) != _DATE_ISO_LEN:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        return None


async def _first_active_chat_id() -> int | None:
    chats = await sheets.read_sheet("chats")
    for row in chats:
        if not _is_active_chat(row.get("active")):
            continue
        raw = str(row.get("chat_id", "")).strip()
        if not raw:
            continue
        try:
            return int(raw)
        except ValueError:
            continue
    return None


async def _send_group_escalation(bot: Bot, text: str) -> bool:
    chat_id = await _first_active_chat_id()
    if chat_id is None:
        logger.warning("task_reminders: no active chat for escalation")
        return False
    try:
        await bot.send_message(chat_id, text[:3500])
        return True
    except TelegramForbiddenError:
        logger.warning("task_reminders: escalation forbidden chat_id=%s", chat_id)
    except TelegramBadRequest as exc:
        logger.warning("task_reminders: escalation bad request chat_id=%s %s", chat_id, exc)
    except Exception as exc:
        logger.error(
            "task_reminders: escalation failed chat_id=%s error=%s",
            chat_id,
            exc,
            exc_info=True,
        )
    return False


async def run_task_due_reminders(bot: Bot, now: datetime) -> int:
    """
    Pending tasks with due_date <= today: at most one DM per calendar day per task,
    up to settings.task_reminder_max_sends total. After task_reminder_escalate_after
    reminders, one brief message to the primary active work chat (if configured).
    """
    today = now.date()
    today_iso = today.isoformat()
    max_sends = int(settings.task_reminder_max_sends)
    escalate_after = min(int(settings.task_reminder_escalate_after), max_sends)

    sent = 0
    try:
        rows = await sheets.read_sheet("tasks")
        employees = await sheets.read_sheet("employees")
    except Exception as exc:
        logger.error("run_task_due_reminders: read tasks failed: %s", exc, exc_info=True)
        return 0

    employee_names = [
        str(r.get("name", "")).strip()
        for r in employees
        if str(r.get("name", "")).strip()
    ]

    for offset, row in enumerate(rows):
        row_index = offset + 2
        status = str(row.get("status", "")).strip()
        if is_closed_status(status):
            continue
        if normalize_status(status) == TASK_STATUS_REVIEW:
            continue

        due = _parse_due_date(row.get("due_date"))
        if due is None or due > today:
            continue

        task_id = str(row.get("task_id", "")).strip()
        title = str(row.get("title", "")).strip()
        assigned_to = str(row.get("assigned_to", "")).strip()
        notes = str(row.get("notes", ""))

        if not task_id or not assigned_to:
            continue

        resolved = resolve_employee_reference(assigned_to, employees)
        if resolved.ok:
            assigned_to = resolved.canonical_name
        else:
            fuzzy = match_employee_name(assigned_to, employee_names)
            if fuzzy:
                assigned_to = fuzzy
            else:
                if _unresolved_assignee_marked(notes):
                    continue
                logger.warning(
                    "task_reminders: unresolved assignee task_id=%s assigned_to=%r: %s",
                    task_id,
                    row.get("assigned_to"),
                    resolved.error,
                )
                try:
                    marked = dict(row)
                    marked["notes"] = _append_lines(
                        notes,
                        _UNRESOLVED_ASSIGNEE_MARKER,
                        f"assigned_to={row.get('assigned_to', '')!s}",
                    )
                    await sheets.update_row("tasks", row_index, marked)
                except Exception as mark_exc:
                    logger.error(
                        "task_reminders: mark unresolved failed task_id=%s %s",
                        task_id,
                        mark_exc,
                        exc_info=True,
                    )
                continue

        if _sent_reminder_today(notes, today_iso):
            continue

        try:
            prev_count = int(str(row.get("reminder_count", "0")).strip() or "0")
        except ValueError:
            prev_count = 0

        if prev_count >= max_sends:
            continue

        due_s = str(row.get("due_date", "")).strip()[:_DATE_ISO_LEN]
        overdue_note = " (срок уже прошёл)" if due < today else ""

        body = (
            f"Напоминание по задаче{overdue_note}:\n"
            f"{title}\n"
            f"Срок: {due_s}\n"
            f"ID задачи: `{task_id}`\n\n"
            "Ответь **реплаем на это сообщение** текстом или фото, когда будет готово."
        )

        dm = await agent_tools.send_dm_to_employee(assigned_to, body[:3400])
        if not dm.get("ok"):
            logger.warning(
                "task_reminders: DM failed task_id=%s employee=%s error=%s",
                task_id,
                assigned_to,
                dm.get("error"),
            )
            continue

        new_count = prev_count + 1
        new_notes = _append_lines(notes, _reminder_marker(today_iso))

        escalated = False
        if new_count >= escalate_after and not _escalation_sent(new_notes):
            esc_text = (
                "Эскалация по задаче (несколько напоминаний без закрытия):\n"
                f"Исполнитель: {assigned_to}\n"
                f"Задача: {title}\n"
                f"Срок: {due_s}\n"
                f"ID: `{task_id}`"
            )
            escalated = await _send_group_escalation(bot, esc_text)
            if escalated:
                new_notes = _append_lines(new_notes, _GROUP_ESCALATION_MARKER)

        updated = dict(row)
        updated["reminder_count"] = str(new_count)
        updated["notes"] = new_notes

        try:
            await sheets.update_row("tasks", row_index, updated)
            sent += 1
            logger.info(
                "task_reminders: sent task_id=%s employee=%s count=%s escalated=%s",
                task_id,
                assigned_to,
                new_count,
                escalated,
            )
        except Exception as exc:
            logger.error(
                "task_reminders: update_row failed task_id=%s error=%s",
                task_id,
                exc,
                exc_info=True,
            )

    if sent:
        logger.info("task_reminders: finished sent=%s date=%s", sent, today_iso)
    return sent
