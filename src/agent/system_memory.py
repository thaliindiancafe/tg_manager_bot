"""System rows for memory_facts (agent context, not user-facing rules)."""

from __future__ import annotations

import logging

from src.google import sheets

logger = logging.getLogger(__name__)

# employee column -> fact text (upserted by scripts/seed_system_memory.py)
SYSTEM_MEMORY_FACTS: dict[str, str] = {
    "_system_product": (
        "Реализовано (май 2026): поручения в ЛС (чеклист, фото, approve/reject); напоминания по "
        "сроку и эскалация в чат; график 07:00 МСК; база знаний Drive 08:00; два календаря; "
        "Google Tasks OAuth + авто-список при create_task/delegate; импорт GT→Sheets каждые 5 мин; "
        "группы: ответ только при @бота или реплае (GROUP_AGENT_REQUIRE_MENTION); "
        "автоматизации; save_fact; память между чатами."
    ),
    "_system_calendar": (
        "Google Calendar: create_event → CALENDAR_EVENTS_ID + лист events. "
        "Утренний синк events (07:05 МСК по умолчанию) — текущий месяц из основного + "
        "«Мероприятия» в лист events. Чтение: get_events_for_dates (sheet + live API). "
        "CALENDAR_ID, CALENDAR_EVENTS_ID в .env; service account с правом изменения."
    ),
    "_system_tasks": (
        "Google Tasks (OAuth Gmail клиента): create_task/delegate пишут в список сотрудника. "
        "Если google_tasks_id пуст — бот ищет список по имени или создаёт новый и прописывает в employees. "
        "Импорт открытых задач в лист tasks каждые 5 мин (GOOGLE_TASKS_SHEETS_SYNC). "
        "Привязка списков Tasks к employees — ежедневно 07:10 МСК (GOOGLE_TASKS_LISTS_SYNC); "
        "новые списки без сотрудника добавляются в employees. "
        "Поручение: @username в тексте (например @asimhayatkhan). "
        "OAuth один раз: google_tasks_oauth_setup.py."
    ),
    "_system_knowledge": (
        "База знаний: файлы в папке DRIVE_KNOWLEDGE_FOLDER_ID на Drive. Индексация каждый день "
        "в 08:00 МСК и по запросу sync_knowledge_folder. На вопросы про меню, регламенты, "
        "инструкции — сначала search_knowledge, не выдумывать. Google Doc и PDF поддерживаются."
    ),
    "_system_schedule": (
        "График смен: лист schedule в таблице бота обновляется автоматически каждый день в 07:00 "
        "МСК из вкладки SOURCE_SCHEDULE_SHEET_NAME (по умолчанию «График Текущий месяц») в SOURCE. "
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
