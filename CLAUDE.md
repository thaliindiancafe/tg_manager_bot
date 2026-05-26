# tg-manager-agent

## Что это
Telegram AI-агент для управления командой ресторана в Москве.
Аналог Mira, но с общей памятью между чатами.
Разрабатывается как тиражируемый продукт (мультитенантность с client_id).

## Стек
- Python 3.11
- aiogram 3.20.0 — Telegram бот (async, webhook)
- google-genai — Gemini 2.5 Flash-Lite (НЕ google-generativeai — устарел)
- google-api-python-client — Sheets, Calendar, Tasks, Drive
- APScheduler 3.10 — планировщик задач
- Бегет VPS — деплой, systemd + uvicorn

## Структура БД (Google Sheets)
Два файла Google Sheets:
1. Старая таблица клиента (SOURCE_SPREADSHEET_ID) — только чтение,
   клиент ведёт как привыкла, бот забирает данные каждое утро
2. Новая таблица бота (SPREADSHEET_ID) — 10 листов:
   - schedule — плоский график смен (синхронизируется из старой таблицы)
   - tasks — задачи сотрудников
   - events — мероприятия
   - chats — список чатов бота
   - employees — справочник сотрудников (хранить telegram_user_id числовой)
   - automations — динамические автоматизации
   - memory_facts — долгосрочные факты без срока давности
   - memory_history — история диалогов, последние 30 на чат
   - knowledge_sources — метаданные проиндексированных файлов Drive
   - knowledge_chunks — текстовые фрагменты для RAG-поиска

## Ключевые сценарии
1. Клиент пишет боту запрос → бот отвечает (нет автоматических рассылок)
2. Клиент настраивает автоматизации через чат → пишется в лист automations
3. run_automations() каждые 5 минут проверяет лист automations и выполняет
4. Клиент говорит "запомни" → save_fact() → пишет в memory_facts
5. Клиент кладёт файлы в папку Drive → RAG-поиск (`search_knowledge`) и чтение одного файла (`read_drive_document`)
6. Каждый день в 07:00 → `sync_schedule()` читает вкладку клиента,
   парсит **только текущий календарный месяц**, перезаписывает лист `schedule`
7. Каждый день в 08:00 → `sync_knowledge_from_drive()` индексирует папку `DRIVE_KNOWLEDGE_FOLDER_ID`

## Синхронизация графика (автообновление смен)
- **Код:** `sync_schedule()` в `src/scheduler/automations.py`; cron в `src/scheduler/runner.py` — **07:00** по `TIMEZONE` (обычно Europe/Moscow).
- **Источник:** лист `SOURCE_SCHEDULE_SHEET_NAME` (по умолчанию `График Текущий месяц`) в `SOURCE_SPREADSHEET_ID`; чтение/запись через `src/google/sheets.py` (`read_source_sheet_values`, `replace_schedule_rows`).
- **Парсинг:** общий модуль `src/utils/schedule_parser.py` → `parse_schedule_grid()` (та же логика, что CLI `scripts/migrate_schedule.py`).
- **Память для агента:** после успешной записи `schedule` выполняется upsert строки в `memory_facts` (`employee=_system_schedule`) с кратким правилом про синк и использование `get_schedule_for_dates`.
- **Новые блоки должностей в исходнике:** базовые роли в `DEFAULT_ROLE_CATEGORIES` в парсере; доп. алиасы — JSON в `.env` `SCHEDULE_ROLE_CATEGORY_ALIASES_JSON` (ключи в нижнем регистре, как в ячейке A).
- **Ответы агента по графику:** tool `get_schedule_for_dates` (preset today/tomorrow/yesterday/none + явные даты `YYYY-MM-DD`) — только запрошенные дни.

## Поручения сотрудникам (roadmap)

Полный пошаговый план и журнал внедрения: **`docs/delegation-roadmap.md`**.  
Фазы 1–5: см. журнал в roadmap. **Фаза 5:** статусы `awaiting_proof`/`review`, **`submit_task_proof`**, проверка чеклиста (Vision + JSON в **`__TASK_PROOF_REPORT__`**), **`approve_task`** / **`reject_task_proof`**, inline-кнопки выбора задачи в личке.

## Google Calendar (два календаря)
- **Код:** `src/google/calendar.py`, `src/scheduler/events_sync.py`, `src/google/calendar_targets.py`.
- **Запись** `create_event` → `CALENDAR_EVENTS_ID` (календарь «Мероприятия») + строка в лист **events**.
- **Утренний синк** `sync_events_from_google_calendars` — **07:05** МСК (по умолчанию): лист **events** = события **текущего месяца** из основного + «Мероприятия» (как график в 07:00).
- **Чтение** `get_events_for_dates` → лист **events** + live API (`calendar_live`) без дублей.
- Service account: доступ «Вносить изменения в мероприятия» на оба календаря; чтение — OAuth или SA.
- Env: `CALENDAR_ID`, `CALENDAR_EVENTS_ID`, `EVENTS_SYNC_ENABLED`, `EVENTS_SYNC_HOUR`, `EVENTS_SYNC_MINUTE`.

## Google Tasks (личный Gmail, OAuth)
- **OAuth:** `src/google/oauth_credentials.py`, `GOOGLE_TASKS_*` в `.env`.
- **Привязка списков:** `tasklist_employees_sync.py` — cron **07:10** МСК (`GOOGLE_TASKS_LISTS_SYNC_*`); новые списки Tasks → строка в `employees` без дублей.
- **Скрипт (ручной):** `sync_tasklists_to_employees.py --apply`.
- **Поток:** `create_task` / `delegate_private_reminder` → лист `tasks` + дубль в Google Tasks.
- **Авто-список:** если у сотрудника нет `google_tasks_id` — `ensure_tasklist_for_employee_row` в `tasklist_resolve.py` ищет список по имени или **создаёт** новый и пишет id в `employees`.
- **Импорт:** `google_tasks_sheets_sync.py` каждые 5 мин с открытых списков в `tasks` (`GOOGLE_TASKS_SHEETS_SYNC_ENABLED`).
- Сопоставление исполнителя: имя, должность, `@username` в фразе («Асим @asimhayatkhan»).
- Закрытие/срок: `google_task_id:` в notes → complete/update в Tasks.

## Групповые чаты (антифлуд)
- **Код:** `src/bot/group_gate.py`; в `text.py`, `photo.py`, `status.py` — без @бота / реплая агент не вызывается.
- **Env:** `GROUP_AGENT_REQUIRE_MENTION=true` (по умолчанию); `false` — старое поведение (отвечает на всё).
- Планировщик и `send_brief_to_primary_work_chat` в группу не затрагиваются.

## Напоминания и эскалация по задачам
- **Код:** `src/scheduler/task_reminders.py`, вызов из `run_automations`.
- **Не каждые 5 мин:** проверка листа `tasks` только в часы **TASK_REMINDER_HOURS** (по умолчанию `10,18` МСК); синк Google Tasks→Sheets по-прежнему каждые 5 мин.
- **ЛС:** не чаще 1 раза в календарный день на задачу, макс. **TASK_REMINDER_MAX_SENDS** (3).
- **Эскалация в общий чат:** **один раз** на задачу после **TASK_REMINDER_ESCALATE_AFTER** (2) напоминаний без закрытия; маркер `__TASK_GROUP_ESCALATION__` в notes.
- **Битый исполнитель** (`assigned_to` не в employees): задача помечается `__TASK_REMINDER_UNRESOLVED_ASSIGNEE__`, напоминания не шлются (исправить имя в Sheets или `status=done`).
- Сообщения «сотрудник не найден» в группе от **ИИ** при поручении — не эскалация; см. **GROUP_AGENT_REQUIRE_MENTION**.
- В группе ответы короткие (блок в `prompt.py` + `GROUP_CHAT_MODE_SECTION` в `client.py`).

## База знаний (RAG)
- **Код:** `src/agent/knowledge/`; cron `sync_knowledge_from_drive` в `src/scheduler/knowledge_sync.py` — **08:00** МСК.
- **Папка:** `DRIVE_KNOWLEDGE_FOLDER_ID`; эмбеддинги на диске VPS: `data/knowledge_embeddings/`.
- **Tools:** `search_knowledge`, `sync_knowledge_folder`, `list_knowledge_sources`.
- **Память:** `memory_facts` `employee=_system_knowledge` (обновляется при синке); сиды: `scripts/seed_system_memory.py`, `src/agent/system_memory.py`.

## Память
- memory_facts: постоянные факты, загружаются в каждый запрос целиком
- `_system_schedule`, `_system_knowledge`, `_system_calendar`, `_system_tasks`, `_system_product` — служебные строки (`system_memory.py`, `scripts/seed_system_memory.py`)
- memory_history: последние 30 сообщений **текущего** chat_id; очищается раз в 30 дней
- **Сквозная память (уровень 2):** в каждый запрос подмешиваются до **`MEMORY_CROSS_CHAT_LIMIT`** (по умолчанию 20) последних реплик из **других** чатов — `get_recent_history_other_chats`, форматирование `src/agent/cross_chat_memory.py`, блок в system prompt. Env: **`MEMORY_CROSS_CHAT_*`**. Отключить: `MEMORY_CROSS_CHAT_ENABLED=false`.
- НЕ хранить состояние в памяти процесса — только в Sheets

## Мультитенантность
Каждый клиент = отдельный SPREADSHEET_ID в .env
В будущем: отдельная таблица с client_id для SaaS-версии

## Часовой пояс
Europe/Moscow — все cron задачи работают по московскому времени

## Деплой
Бегет VPS, Ubuntu
systemd сервис с Restart=always, RestartSec=10
uvicorn для webhook
Telegram webhook URL: https://домен/webhook

## Важно перед запуском
- Privacy Mode отключён в @BotFather (Turn off)
- Бот удалён и снова добавлен во все чаты после отключения Privacy Mode
- Service account добавлен в обе таблицы (старую — Читатель, новую — Редактор)
- Service account добавлен в Google Calendar и папку Drive

## Переменные окружения
BOT_TOKEN, GEMINI_API_KEY, SPREADSHEET_ID, SOURCE_SPREADSHEET_ID,
SOURCE_SCHEDULE_SHEET_NAME (опц.), SCHEDULE_ROLE_CATEGORY_ALIASES_JSON (опц.),
GOOGLE_CREDENTIALS_JSON, DRIVE_KNOWLEDGE_FOLDER_ID, KNOWLEDGE_SYNC_*,
CALENDAR_ID, CALENDAR_EVENTS_ID, EVENTS_SYNC_*, GOOGLE_TASKS_USE_OAUTH, GOOGLE_TASKS_OAUTH_*,
GOOGLE_TASKS_SHEETS_SYNC_ENABLED, GROUP_AGENT_REQUIRE_MENTION,
WEBHOOK_URL, WEBHOOK_SECRET, TIMEZONE, DEV_MODE

## Отложенные решения (вернуться позже)

### Лист `events` в Sheets → только Google Calendar (2026-05-20)

**Запрос клиента:** вести мероприятия без вкладки events в таблице бота; всё через Google Calendar
(там синхронизация с другими почтами — для неё это основная база).

**Сейчас:** `create_event` пишет в Calendar + лист events; cron `sync_events_from_google_calendars`
зеркалит месяц в events. Клиенту можно сказать: вкладку не заполнять вручную.

**Отложенная доработка:** режим calendar-only — убрать запись/синк в лист events, оставить только
`src/google/calendar.py` + `get_events_for_dates` из API. Память агента: `_system_deferred_events_sheet`.

### Задачи клиента: Sheets vs Google Tasks API (2026-05-15)

**Сценарий:** клиент пишет задачи боту в Telegram → бот сохраняет и выполняет/напоминает
в нужный момент (статус, due_date, automations).

**Решение (актуально):**
- **Основное хранилище** — лист `tasks` в Google Sheets.
- **Дублирование в Google Tasks** — OAuth личного Gmail клиента (`GOOGLE_TASKS_*`),
  списки по именам в `employees.google_tasks_id` (см. `docs/specs/google-tasks-sync-spec.md`).
- **Напоминания по сроку** — Telegram ЛС (`run_task_due_reminders`), не push из Tasks.
- Импорт Tasks → Sheets и двусторонний синк — **не реализованы**.