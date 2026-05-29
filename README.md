# tg-manager-agent

Telegram AI-агент для управления командой ресторана. Аналог Mira с общей памятью между чатами. На базе **Gemini 2.5 Flash-Lite** и **Google Sheets** как единой базы данных.

Клиент общается с ботом в Telegram — задаёт задачи, смотрит график, настраивает напоминания. Бот запоминает факты, работает в нескольких чатах одновременно и выполняет действия через tool use (Sheets, Calendar, Drive).

---

## Требования

| Компонент | Описание |
|-----------|----------|
| **Python 3.11+** | Локальная разработка и сервер |
| **Google Cloud** | Service account: Sheets, Calendar, Tasks, Drive API |
| **Gemini API** | Ключ Google AI Studio |
| **Telegram Bot** | Токен от @BotFather |
| **Beget VPS** | Production: Ubuntu, webhook, systemd (опционально для dev — только polling) |

---

## Быстрый старт

### 1. Клонировать репозиторий

```bash
git clone https://github.com/thaliindiancafe/tg_manager_bot.git tg-manager-agent
cd tg-manager-agent
```

### 2. Создать `.env` по образцу

```bash
cp .env.example .env
# Отредактируйте .env — токены, ID таблиц, пути
```

### 3. Положить ключ Google

```bash
mkdir -p secrets
# Скопируйте JSON ключ service account:
# secrets/service_account.json
```

Service account должен иметь доступ:
- **Новая таблица** (`SPREADSHEET_ID`) — роль **Редактор**
- **Старая таблица** (`SOURCE_SPREADSHEET_ID`) — роль **Читатель**
- **Google Calendar** — доступ к календарю
- **Google Drive** — папка `DRIVE_KNOWLEDGE_FOLDER_ID`

### 4. Проверить Google API

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt

python scripts/test_google_api.py
```

### 5. Создать листы и заголовки в таблице бота

```bash
python scripts/setup_sheets.py
```

### 6. Мигрировать график из старой таблицы клиента

```bash
# Сначала тест парсинга (первые 20 строк)
python scripts/migrate_schedule.py --test

# Запись в лист schedule
python scripts/migrate_schedule.py
```

### 7. Запуск в dev-режиме (polling)

В `.env` установите:

```env
DEV_MODE=true
```

```bash
python main.py
```

Бот отвечает в Telegram через long polling. Планировщик автоматизаций запускается автоматически.

---

## Структура проекта

```
tg-manager-agent/
├── main.py                      # Точка входа: polling (dev) или webhook (prod)
├── requirements.txt             # Зависимости Python
├── .env.example                 # Шаблон переменных окружения
│
├── src/
│   ├── config.py                # Настройки из .env (pydantic-settings)
│   │
│   ├── bot/
│   │   ├── router.py            # Главный Router: порядок handlers
│   │   └── handlers/
│   │       ├── start.py         # /start — регистрация сотрудника
│   │       ├── status.py        # «сделано», «перенеси» → агент
│   │       ├── photo.py         # Фото → Gemini Vision → агент
│   │       └── text.py          # Остальные текстовые сообщения
│   │
│   ├── agent/
│   │   ├── client.py            # Gemini: call_agent(), describe_photo()
│   │   ├── prompt.py            # SYSTEM_PROMPT на русском
│   │   └── tools.py             # Tool use: задачи, график, события, память
│   │
│   ├── google/
│   │   ├── sheets.py            # Google Sheets (память, задачи, автоматизации)
│   │   ├── calendar.py          # Google Calendar
│   │   ├── tasks.py             # Google Tasks (опционально)
│   │   └── drive.py             # Google Drive (документы)
│   │
│   └── scheduler/
│       ├── runner.py            # APScheduler: start/stop
│       └── automations.py       # run_automations() каждые 5 минут
│
├── scripts/
│   ├── test_google_api.py       # Проверка подключения к Google API
│   ├── setup_sheets.py          # Создание листов + тест парсинга графика
│   ├── init_sheets.py           # Только инициализация листов (альтернатива)
│   └── migrate_schedule.py      # Миграция графика из старой таблицы
│
├── deploy/
│   ├── tg-agent.service         # systemd unit для VPS
│   └── README_deploy.md         # Инструкция деплоя на Ubuntu
│
└── secrets/
    └── service_account.json     # Ключ Google (не коммитить!)
```

---

## Переменные окружения

| Переменная | Обязательно | Описание |
|------------|-------------|----------|
| `BOT_TOKEN` | да | Токен Telegram-бота от @BotFather |
| `GEMINI_API_KEY` | да | API-ключ Google Gemini |
| `SPREADSHEET_ID` | да | ID новой таблицы бота (8 листов) |
| `SOURCE_SPREADSHEET_ID` | да | ID старой таблицы клиента (только чтение, график) |
| `GOOGLE_CREDENTIALS_JSON` | да | Путь к `secrets/service_account.json` |
| `DRIVE_KNOWLEDGE_FOLDER_ID` | да | ID папки Drive с документами |
| `WEBHOOK_URL` | prod | URL webhook, напр. `https://domain.com/webhook` |
| `WEBHOOK_SECRET` | prod | Секрет для заголовка Telegram webhook |
| `TIMEZONE` | да | Часовой пояс cron, напр. `Europe/Moscow` |
| `DEV_MODE` | нет | `true` = polling, `false` = webhook (по умолчанию `true`) |

Опционально в коде (не в `.env.example`):

| Переменная | Описание |
|------------|----------|
| `CALENDAR_ID` | ID календаря Google (по умолчанию `primary`) |

---

## Как клиент управляет ботом

Бот понимает обычный русский текст в личке и групповых чатах. Примеры:

**График и смены**
```
кто сегодня работает?
```

**Задачи**
```
создай задачу для Ивана: проверить кассу до 18:00
```

**Статус задачи**
```
сделано
готово
перенеси на завтра
```

**Память (долгосрочные факты)**
```
запомни что Иван не работает по воскресеньям
```

**Автоматизации (расписание)**
```
напоминай каждую пятницу в 18:00 про отчёт
```

**Мероприятия**
```
добавь встречу 20 мая в 15:00 — собрание команды
```

**Документы Drive**
```
прочитай документ и ответь по регламенту
```
(если в памяти сохранён `file_id` документа)

---

## Стоимость использования

| Сервис | Ориентир |
|--------|----------|
| **Gemini 2.5 Flash-Lite** | ~$2–5/мес при активном чате ресторана |
| **Google Sheets / Drive** | Бесплатно в рамках лимитов Google |
| **Beget VPS** | По тарифу хостинга |
| **Telegram Bot API** | Бесплатно |

Точная стоимость Gemini зависит от числа сообщений и вызовов tool use.

---

## Деплой на production

Подробная инструкция: **[deploy/README_deploy.md](deploy/README_deploy.md)**

Кратко:
- `DEV_MODE=false` в `.env`
- Nginx + SSL (Let's Encrypt) → прокси на порт `8080`
- systemd: `deploy/tg-agent.service`
- Регистрация webhook через Telegram API

```bash
systemctl enable tg-agent
systemctl start tg-agent
journalctl -u tg-agent -f
```

---

## Лицензия

MIT (или укажите свою лицензию при публикации репозитория).
