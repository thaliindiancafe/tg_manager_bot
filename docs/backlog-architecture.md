# Backlog: архитектура трёх направлений

Обзор связей между фичами, не входящими в MVP поручений. Детальные ТЗ — в `docs/specs/`.

| ТЗ | Файл | Суть |
|----|------|------|
| База знаний + RAG + Drive 08:00 | [specs/knowledge-rag-spec.md](specs/knowledge-rag-spec.md) | **Реализовано** (cron 08:00, tools) |
| Recurring из Google Doc | [specs/recurring-events-spec.md](specs/recurring-events-spec.md) | План |
| Google Tasks сотрудников | [specs/google-tasks-sync-spec.md](specs/google-tasks-sync-spec.md) | План |

## Порядок внедрения

1. Knowledge RAG (заменяет «Notebook LM + обучи по ссылке» для клиента)
2. Recurring events (календарь менеджера)
3. Google Tasks (только при Google Workspace + DWD)

## Общие принципы

- Источник правды для операций бота — **Google Sheets**, кроме векторов (файл `data/knowledge_embeddings/` на диске VPS).
- Все cron — **Europe/Moscow** через `src/scheduler/runner.py`.
- Новые листы добавляются через `init_all_sheets()` / `scripts/setup_sheets.py`.
