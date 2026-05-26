# Backlog: архитектура трёх направлений

Обзор связей между фичами, не входящими в MVP поручений. Детальные ТЗ — в `docs/specs/`.

| ТЗ | Файл | Суть |
|----|------|------|
| База знаний + RAG + Drive 08:00 | [specs/knowledge-rag-spec.md](specs/knowledge-rag-spec.md) | **Реализовано** (cron 08:00, tools) |
| Recurring из Google Doc | [specs/recurring-events-spec.md](specs/recurring-events-spec.md) | План |
| Google Tasks сотрудников | [specs/google-tasks-sync-spec.md](specs/google-tasks-sync-spec.md) | План |
| Убрать лист `events`, только Google Calendar | — | **Отложено** (запрос клиента 2026-05-20) |

## Отложено: calendar-only вместо листа `events`

Клиент: мероприятия только в Google Calendar (синхронизация с другими почтами), вкладку events в таблице бота не вести.

**Сейчас:** зеркало `events` (create_event + sync 07:05). **Цель:** чтение/запись только Calendar API.

Память: `memory_facts` → `_system_deferred_events_sheet` (`scripts/seed_system_memory.py`).

## Порядок внедрения

1. Knowledge RAG (заменяет «Notebook LM + обучи по ссылке» для клиента)
2. Recurring events (календарь менеджера)
3. Google Tasks (только при Google Workspace + DWD)

## Общие принципы

- Источник правды для операций бота — **Google Sheets**, кроме векторов (файл `data/knowledge_embeddings/` на диске VPS).
- Все cron — **Europe/Moscow** через `src/scheduler/runner.py`.
- Новые листы добавляются через `init_all_sheets()` / `scripts/setup_sheets.py`.
