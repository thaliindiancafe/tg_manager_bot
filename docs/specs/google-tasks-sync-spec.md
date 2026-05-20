# ТЗ: Синхронизация Google Tasks сотрудников

## Цель

Дублировать поручения из листа `tasks` в приложение **Google Tasks** сотрудника (опционально), не заменяя Sheets как источник правды.

## Текущее состояние

- Модуль [`src/google/tasks.py`](../../src/google/tasks.py): create / complete / update deadline.
- [`create_task`](../../src/agent/tools.py): при заполненном `employees.google_tasks_id` создаёт задачу в Tasks.
- **Двусторонний синк и список задач из Tasks — нет.**

## Ограничение Google

Service account **не видит** личные Tasks обычного Gmail. Варианты:

| Вариант | Требования | Рекомендация |
|---------|------------|--------------|
| A. Только Sheets + ЛС бота | Ничего | **По умолчанию** |
| B. Общий task list SA | Один список на всех | Редко устраивает |
| C. Domain-Wide Delegation | Google Workspace, админ | Для корпоративных клиентов |
| D. OAuth аккаунта клиента (личный Gmail) | OAuth Desktop + token в secrets | **Реализовано** |

## Env (фаза Workspace)

| Переменная | Описание |
|------------|----------|
| `GOOGLE_WORKSPACE_ADMIN_EMAIL` | Субъект для impersonation |
| `GOOGLE_TASKS_SYNC_ENABLED` | `true` / `false` |
| `GOOGLE_TASKS_DELEGATION_SCOPES` | tasks + admin (настроено в Admin Console) |

## Лист `employees` (расширение)

| Колонка | Описание |
|---------|----------|
| `google_tasks_id` | ID task list (уже есть) |
| `google_account_email` | **новое** — email для `with_subject()` |

## Поток (фаза C)

1. `create_task` / `delegate_private_reminder` → Sheets.
2. Если `google_account_email` + `google_tasks_id` → Tasks API от имени пользователя.
3. `complete_task` / `approve_task` → complete в Tasks по `google_task_id:` в notes.

## Не в MVP

- Импорт Tasks → Sheets
- Конфликт-резолюшн при правке в приложении Tasks

## Статус

**Реализовано (личный Gmail):** OAuth Desktop, `src/google/oauth_credentials.py`, `tasks.py` через OAuth;
`create_task` / `delegate_private_reminder` дублируют в Tasks; `sync_tasklists_to_employees.py --apply`.

**Реализовано (2026-05):** `sync_google_tasks_to_sheets()` в `src/scheduler/google_tasks_sheets_sync.py` — каждые 5 мин с `run_automations`; env `GOOGLE_TASKS_SHEETS_SYNC_ENABLED=true`. Открытые задачи из списков сотрудников (+ «Мои задачи» руководителя) → лист `tasks`; маркер `google_task_id:` + `__GT_SHEETS_IMPORT__:v1` в notes.

**Не реализовано:** синк закрытых задач из Google в done (кроме статуса при обновлении открытых); Workspace DWD.
