# ТЗ: Регулярные мероприятия из Google Doc → календарь

## Цель

Таблица/текст в Google Doc описывает повторяющиеся события («каждый понедельник 10:00 планёрка») → серии в **календаре менеджера** (Google Calendar API + RRULE).

## Env

| Переменная | Обязательно | По умолчанию | Описание |
|------------|-------------|--------------|----------|
| `MANAGER_CALENDAR_ID` | да | — | ID календаря (расшарен SA с правом записи) |
| `RECURRING_EVENTS_DOC_ID` | да | — | Google Doc file_id с правилами |
| `RECURRING_SYNC_ENABLED` | нет | `true` | Cron синка |
| `RECURRING_SYNC_HOUR` | нет | `8` | Час (после knowledge) |
| `RECURRING_SYNC_MINUTE` | нет | `30` | Минута |
| `TIMEZONE` | да | `Europe/Moscow` | TZ для RRULE |

## Лист `recurring_events`

| Колонка | Описание |
|---------|----------|
| `rule_id` | UUID |
| `title` | Название |
| `time` | HH:MM |
| `rrule` | RFC5545 без префикса, напр. `FREQ=WEEKLY;BYDAY=MO` |
| `description` | Текст |
| `calendar_id` | Куда создано |
| `gcal_event_id` | ID серии в Calendar |
| `source_doc_id` | Drive file_id Doc |
| `content_hash` | Хеш Doc для diff |
| `active` | true/false |

## Парсинг Doc

**MVP:** таблица в Doc:

| title | time | recurrence | description |
|-------|------|------------|-------------|
| Планёрка | 10:00 | weekly:MO | ... |

`recurrence`: `weekly:MO`, `monthly:1`, `daily`.

**Альтернатива:** Gemini JSON из свободного текста + превью руководителю.

## Calendar API

```python
body["recurrence"] = [f"RRULE:{rrule}"]
```

`get_today_events` уже с `singleEvents=True` — экземпляры видны.

## Cron

`sync_recurring_events_from_doc()` — 08:30 МСК (после графика 07:00 и knowledge 08:00).

## Tools

- `sync_recurring_events_now`
- `list_recurring_events`

## Риски

- Календарь SA ≠ личный календарь человека — нужен shared calendar.
- Смена RRULE: проще удалить серию и создать заново.

## Статус

Не реализовано. Зависит от согласования формата Doc с клиентом.
