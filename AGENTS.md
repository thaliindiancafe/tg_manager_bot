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
- **Код:** `src/google/calendar.py`, маршрутизация `src/google/calendar_targets.py`.
- **Запись** `create_event` → `CALENDAR_EVENTS_ID` (календарь «Мероприятия»).
- **Чтение** `get_today_events` → основной (`CALENDAR_ID` / `CALENDAR_PRIMARY_ID`) + events.
- Service account: доступ «Вносить изменения в мероприятия» на оба календаря.
- Env: `CALENDAR_ID`, `CALENDAR_EVENTS_ID`, опц. `CALENDAR_*_LABEL`.

## Google Tasks (личный Gmail, OAuth)
- **OAuth:** `src/google/oauth_credentials.py`, `GOOGLE_TASKS_*` в `.env`.
- **Скрипты:** `google_tasks_oauth_setup.py`, `sync_tasklists_to_employees.py --apply`.
- **Поток:** Sheets `tasks` + дубль в Tasks при заполненном `employees.google_tasks_id`.
- Закрытие/срок: `google_task_id:` в notes → complete/update в Tasks.

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
CALENDAR_ID, CALENDAR_EVENTS_ID, GOOGLE_TASKS_USE_OAUTH, GOOGLE_TASKS_OAUTH_*,
WEBHOOK_URL, WEBHOOK_SECRET, TIMEZONE, DEV_MODE

## Отложенные решения (вернуться позже)

### Задачи клиента: Sheets vs Google Tasks API (2026-05-15)

**Сценарий:** клиент пишет задачи боту в Telegram → бот сохраняет и выполняет/напоминает
в нужный момент (статус, due_date, automations).

**Решение (актуально):**
- **Основное хранилище** — лист `tasks` в Google Sheets.
- **Дублирование в Google Tasks** — OAuth личного Gmail клиента (`GOOGLE_TASKS_*`),
  списки по именам в `employees.google_tasks_id` (см. `docs/specs/google-tasks-sync-spec.md`).
- **Напоминания по сроку** — Telegram ЛС (`run_task_due_reminders`), не push из Tasks.
- Импорт Tasks → Sheets и двусторонний синк — **не реализованы**.