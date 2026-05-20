# После приёмки клиента

## Уже в коде (не ждать отдельной разработки)

- Фазы 1–5 поручений: чеклист, фото-проверка, approve/reject, кнопки выбора задачи.
- Сквозная память между чатами (`MEMORY_CROSS_CHAT_*`).

## Сразу после теста

1. Багфиксы по шаблону из `delegation-roadmap.md` (скрин, время, chat_id, task_id).
2. Стабильный хостинг: VPS + systemd + webhook (`deploy/README_deploy.md`).
3. Проверка напоминаний на реальной задаче с `due_date = сегодня`.

## Backlog (по запросу клиента, не смешивать без приоритета)

| Тема | Суть |
|------|------|
| База знаний Mira-style | **Сделано** — Drive RAG 08:00 |
| Google Tasks (колонки по именам) | **Сделано** — OAuth Gmail + `google_tasks_id` |
| Два календаря (основной + Мероприятия) | **Сделано** — `CALENDAR_EVENTS_ID`, чтение обоих |
| Recurring из Doc → Calendar | План — `docs/specs/recurring-events-spec.md` |
| Погода Москва | Tool + Open-Meteo |
| Notebook LM по ссылке | Не нужен — папка Drive |
| Мультитенантность SaaS | Один код, много `client_id` / таблиц |
