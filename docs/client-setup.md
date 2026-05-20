# Подготовка перед тестом клиента (15 минут)

Чек-лист для администратора / разработчика. Бот должен работать **24/7** на время теста (VPS + webhook или ПК с `python main.py`).

## 1. Google и .env

- [ ] `SPREADSHEET_ID`, `SOURCE_SPREADSHEET_ID`, `GOOGLE_CREDENTIALS_JSON`, `BOT_TOKEN`, `GEMINI_API_KEY` в `.env`
- [ ] Service account: **Редактор** новой таблицы, **Читатель** старой (график)
- [ ] Доступ к Google Calendar и папке Drive (`DRIVE_KNOWLEDGE_FOLDER_ID`)
- [ ] `python scripts/setup_sheets.py` — листы с заголовками
- [ ] `python scripts/test_google_api.py` — все проверки OK
- [ ] По желанию: `python scripts/migrate_schedule.py` — график в лист `schedule`

## 2. Лист employees

| name | telegram_user_id | username | role | active |
|------|------------------|----------|------|--------|
| Иван | (после /start) | @ivan | официант | true |

- **name** — как руководитель говорит боту («Передай **Ивану**…»).
- Каждый сотрудник: личка с ботом → **`/start`** → появится `telegram_user_id`.

## 3. Лист chats

| chat_id | chat_name | active | timezone |
|---------|-----------|--------|----------|
| -1001234567890 | Рабочий чат смены | true | Europe/Moscow |

- **chat_id**: в группе с ботом команда **`/chatid`** (скопировать число).
- **active** = `true` для эскалаций и объявлений в общий чат.

## 4. Telegram

- [ ] Privacy Mode **выключен** в @BotFather
- [ ] Бот **передобавлен** в группы после отключения Privacy Mode
- [ ] Бот запущен: `DEV_MODE=true` → `python main.py` или VPS + webhook

## 5. Внутренний smoke (разработчик)

```bash
python scripts/smoke_acceptance.py
```

Дальше — ручной прогон `docs/client-acceptance-test-ru.md`.
