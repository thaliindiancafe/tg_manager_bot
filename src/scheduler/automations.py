"""Scheduled automations runner."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from aiogram import Bot

from src.agent.client import call_agent
from src.config import settings
from src.google import sheets
from src.scheduler.google_tasks_reminders import run_google_tasks_due_reminders
from src.scheduler.google_tasks_sheets_sync import sync_google_tasks_to_sheets
from src.scheduler.reminder_schedule import (
    parse_reminder_hours_csv,
    should_run_task_reminders_now,
)
from src.scheduler.task_reminders import run_task_due_reminders
from src.utils.schedule_parser import parse_schedule_grid

logger = logging.getLogger(__name__)

SCHEDULE_SYNC_MEMORY_EMPLOYEE = "_system_schedule"

TIME_MATCH_TOLERANCE = timedelta(minutes=2)

WEEKDAY_ALIASES: dict[str, int] = {
    "0": 0,
    "1": 1,
    "2": 2,
    "3": 3,
    "4": 4,
    "5": 5,
    "6": 6,
    "mon": 0,
    "monday": 0,
    "tue": 1,
    "tuesday": 1,
    "wed": 2,
    "wednesday": 2,
    "thu": 3,
    "thursday": 3,
    "fri": 4,
    "friday": 4,
    "sat": 5,
    "saturday": 5,
    "sun": 6,
    "sunday": 6,
    "пн": 0,
    "понедельник": 0,
    "вт": 1,
    "вторник": 1,
    "ср": 2,
    "среда": 2,
    "чт": 3,
    "четверг": 3,
    "пт": 4,
    "пятница": 4,
    "сб": 5,
    "суббота": 5,
    "вс": 6,
    "воскресенье": 6,
}


def _get_tz() -> ZoneInfo:
    return ZoneInfo(settings.timezone)


def _is_active(value: Any) -> bool:
    return str(value).strip().lower() in {"true", "1", "yes", "on"}


def _parse_trigger_time(trigger_time: str) -> tuple[int, int] | None:
    text = trigger_time.strip()
    try:
        parsed = datetime.strptime(text, "%H:%M")
        return parsed.hour, parsed.minute
    except ValueError:
        logger.warning("Invalid trigger_time: %s", trigger_time)
        return None


def _time_matches_now(trigger_time: str, now: datetime) -> bool:
    parsed = _parse_trigger_time(trigger_time)
    if parsed is None:
        return False

    hour, minute = parsed
    scheduled = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    delta = abs(now - scheduled)
    return delta <= TIME_MATCH_TOLERANCE


def _parse_weekday(trigger_day: str) -> int | None:
    key = trigger_day.strip().lower()
    if not key:
        return None
    if key in WEEKDAY_ALIASES:
        return WEEKDAY_ALIASES[key]
    logger.warning("Unknown trigger_day: %s", trigger_day)
    return None


def _should_run_automation(row: dict[str, Any], now: datetime) -> bool:
    trigger_type = str(row.get("trigger_type", "")).strip().lower()
    trigger_time = str(row.get("trigger_time", "")).strip()
    trigger_day = str(row.get("trigger_day", "")).strip()

    if not trigger_time:
        return False

    if trigger_type == "daily":
        return _time_matches_now(trigger_time, now)

    if trigger_type == "weekly":
        weekday = _parse_weekday(trigger_day)
        if weekday is None:
            return False
        if now.weekday() != weekday:
            return False
        return _time_matches_now(trigger_time, now)

    logger.warning(
        "Unsupported trigger_type=%s for automation id=%s",
        trigger_type,
        row.get("id"),
    )
    return False


def _build_agent_message(action: str, params: str) -> str:
    return (
        "[Автоматизация]\n"
        f"Выполни действие: {action}\n"
        f"Параметры: {params}"
    )


async def _get_active_chats() -> list[dict[str, Any]]:
    chats = await sheets.read_sheet("chats")
    return [row for row in chats if _is_active(row.get("active"))]


async def _broadcast_message(bot: Bot, text: str, chats: list[dict[str, Any]]) -> None:
    for chat in chats:
        chat_id_raw = str(chat.get("chat_id", "")).strip()
        if not chat_id_raw:
            continue
        try:
            await bot.send_message(int(chat_id_raw), text)
        except Exception as exc:
            logger.error(
                "Failed to send automation message: chat_id=%s error=%s",
                chat_id_raw,
                exc,
                exc_info=True,
            )


async def run_automations(bot: Bot | None = None) -> int:
    """
    Check automations sheet and run due actions every scheduler tick.

    Returns the number of executed automations.
    """
    close_bot = False
    if bot is None:
        bot = Bot(token=settings.bot_token)
        close_bot = True

    executed = 0

    try:
        now = datetime.now(_get_tz())
        await sync_google_tasks_to_sheets()

        reminder_hours = parse_reminder_hours_csv(settings.task_reminder_hours)
        if should_run_task_reminders_now(
            now,
            reminder_hours,
            window_minutes=settings.task_reminder_window_minutes,
        ):
            await run_task_due_reminders(bot, now)
            await run_google_tasks_due_reminders(bot, now)
        else:
            logger.debug(
                "task_reminders: skipped (outside TASK_REMINDER_HOURS=%s)",
                settings.task_reminder_hours,
            )

        automations = await sheets.read_sheet("automations")
        active_automations = [
            row for row in automations if _is_active(row.get("active"))
        ]

        if not active_automations:
            return executed

        active_chats = await _get_active_chats()
        if not active_chats:
            logger.warning("run_automations: no active chats in sheet 'chats'")
            return executed

        primary_chat_id = int(str(active_chats[0]["chat_id"]))

        for row in active_automations:
            if not _should_run_automation(row, now):
                continue

            action = str(row.get("action", "")).strip()
            params = str(row.get("params", "")).strip()
            automation_id = str(row.get("id", "")).strip()

            try:
                agent_message = _build_agent_message(action, params)
                history = await sheets.get_recent_history(primary_chat_id, limit=30)
                reply = await call_agent(agent_message, primary_chat_id, history)
                await _broadcast_message(bot, reply, active_chats)
                executed += 1
                logger.info(
                    "Automation executed: id=%s action=%s chats=%s",
                    automation_id,
                    action,
                    len(active_chats),
                )
            except Exception as exc:
                logger.error(
                    "Automation failed: id=%s error=%s",
                    automation_id,
                    exc,
                    exc_info=True,
                )

        return executed
    except Exception as exc:
        logger.error("run_automations failed: %s", exc, exc_info=True)
        return executed
    finally:
        if close_bot and bot is not None:
            await bot.session.close()


def _schedule_sync_fact_text() -> str:
    return (
        "График смен: лист schedule в таблице бота обновляется автоматически каждый день в 07:00 "
        f"(часовой пояс {settings.timezone}). Источник — вкладка "
        f"«{settings.source_schedule_sheet_name}» в таблице клиента (SOURCE); "
        "в schedule загружаются строки только за текущий календарный месяц. "
        "На вопросы «кто на смене», «кто работает завтра/вчера» всегда вызывай инструмент "
        "get_schedule_for_dates с preset=today / tomorrow / yesterday или с явными датами "
        "YYYY-MM-DD; не смешивай даты и не опирайся на строки вне запрошенного дня."
    )


async def sync_schedule() -> None:
    """
    Read client sheet tab (SOURCE_SCHEDULE_SHEET_NAME), parse current calendar month,
    replace bot spreadsheet ``schedule`` (row 2+). Intended for daily cron (07:00 Moscow).
    """
    try:
        now = datetime.now(_get_tz())
        sheet_name = settings.source_schedule_sheet_name
        values = await sheets.read_source_sheet_values(sheet_name)
        if not values:
            logger.warning("sync_schedule: no cells in source sheet %r", sheet_name)

        rows = parse_schedule_grid(values, now)
        await sheets.replace_schedule_rows(rows)
        try:
            await sheets.upsert_memory_fact_row(
                SCHEDULE_SYNC_MEMORY_EMPLOYEE,
                _schedule_sync_fact_text(),
            )
        except Exception as mem_exc:
            logger.warning(
                "sync_schedule: schedule OK but memory_facts upsert failed: %s",
                mem_exc,
                exc_info=True,
            )
        logger.info(
            "sync_schedule: replaced schedule with %s rows (source=%r month=%s)",
            len(rows),
            sheet_name,
            now.month,
        )
    except Exception as exc:
        logger.error("sync_schedule failed: %s", exc, exc_info=True)
