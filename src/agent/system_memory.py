"""System rows for memory_facts (agent context, not user-facing rules)."""

from __future__ import annotations

import logging

from src.config import settings
from src.google import sheets
from src.storage import get_store

logger = logging.getLogger(__name__)

# employee column -> fact text (upserted by scripts/seed_system_memory.py)
SYSTEM_MEMORY_FACTS: dict[str, str] = {
    "_system_product": (
        "Репозиторий кода: https://github.com/thaliindiancafe/tg_manager_bot (main). "
        "Деплой: Beget VPS, systemd + uvicorn webhook. "
        "Реализовано (май 2026): поручения в ЛС (чеклист, фото, approve/reject); напоминания по "
        "сроку и эскалация в чат; график 07:00 МСК; база знаний Drive 08:00; два календаря; "
        "Google Tasks OAuth + авто-список при create_task/delegate; импорт GT→Sheets каждые 5 мин; "
        "группы: ответ только при @бота или реплае (GROUP_AGENT_REQUIRE_MENTION); "
        "автоматизации; save_fact; память между чатами."
    ),
    "_system_calendar": (
        "Google Calendar: create_event → CALENDAR_EVENTS_ID. "
        "Чтение: get_events_for_dates — только live API (CALENDAR_ONLY_MODE / STORAGE_BACKEND=db). "
        "Лист events в Sheets не используется. CALENDAR_ID, CALENDAR_EVENTS_ID в .env; "
        "service account или OAuth с calendar.readonly."
    ),
    "_system_tasks": (
        "Google Tasks (OAuth Gmail клиента): create_task/delegate → список сотрудника + метаданные в БД. "
        "Если google_tasks_id пуст — бот ищет список по имени или создаёт новый (employees в Supabase). "
        "GOOGLE_TASKS_SHEETS_SYNC — только при STORAGE_BACKEND=sheets (по умолчанию выкл в db). "
        "Привязка списков Tasks ↔ employees — 07:10 МСК (GOOGLE_TASKS_LISTS_SYNC). "
        "Поручение: @username в тексте. OAuth: google_tasks_oauth_setup.py."
    ),
    "_system_knowledge": (
        "База знаний: файлы в DRIVE_KNOWLEDGE_FOLDER_ID. Индексация 08:00 МСК → "
        "knowledge_sources/knowledge_chunks в Supabase (STORAGE_BACKEND=db). "
        "На вопросы про меню и регламенты — search_knowledge, не выдумывать."
    ),
    "_system_schedule": (
        "График смен: таблица schedule в Supabase (при STORAGE_BACKEND=db) обновляется каждый день "
        "в 07:00 МСК из вкладки SOURCE_SCHEDULE_SHEET_NAME в SOURCE_SPREADSHEET_ID (только чтение). "
        "Для ответов используй get_schedule_for_dates (today/tomorrow/yesterday или даты YYYY-MM-DD)."
    ),
    "_system_deferred_events_sheet": (
        "ОТЛОЖЕНО (запрос клиента 2026-05): убрать лист events в Sheets — мероприятия только "
        "через Google Calendar (там синхронизация с другими почтами; для клиента это основная БД). "
        "Сейчас: create_event → Calendar + events; утренний sync Calendar→events (07:05). "
        "Цель: calendar-only — чтение/запись только API, без replace_events_rows/append_row events."
    ),
}


async def seed_system_memory_facts() -> dict[str, str]:
    """Upsert all SYSTEM_MEMORY_FACTS into memory_facts. Returns employee -> status."""
    results: dict[str, str] = {}
    for employee_key, fact_text in SYSTEM_MEMORY_FACTS.items():
        try:
            if (getattr(settings, "storage_backend", "sheets") or "sheets").strip().lower() == "db":
                await get_store().memory.upsert_fact(employee=employee_key, fact=fact_text)
            else:
                await sheets.upsert_memory_fact_row(employee_key, fact_text)
            results[employee_key] = "ok"
            logger.info("seed_system_memory: upserted %s", employee_key)
        except Exception as exc:
            results[employee_key] = f"error: {exc}"
            logger.error(
                "seed_system_memory failed: employee=%s error=%s",
                employee_key,
                exc,
                exc_info=True,
            )
    return results
