# ТЗ: База знаний (RAG) + автоиндекс Drive 08:00

## Цель

Клиент кладёт файлы в папку Google Drive — бот **сам индексирует** и отвечает по содержимому через семантический поиск (RAG). Аналог «обучи по ссылке / Notebook LM» без отдельного продукта Google.

## Env

| Переменная | Обязательно | По умолчанию | Описание |
|------------|-------------|--------------|----------|
| `DRIVE_KNOWLEDGE_FOLDER_ID` | да* | — | ID папки Drive с документами |
| `KNOWLEDGE_SYNC_ENABLED` | нет | `true` | Включить cron 08:00 |
| `KNOWLEDGE_SYNC_HOUR` | нет | `8` | Час синка (МСК) |
| `KNOWLEDGE_SYNC_MINUTE` | нет | `0` | Минута синка |
| `KNOWLEDGE_CHUNK_MAX_CHARS` | нет | `900` | Размер фрагмента текста |
| `KNOWLEDGE_SEARCH_TOP_K` | нет | `8` | Число фрагментов в ответ агента |
| `KNOWLEDGE_EMBEDDING_MODEL` | нет | `text-embedding-004` | Модель эмбеддингов Gemini |
| `GEMINI_API_KEY` | да | — | Уже используется агентом |

\* Если пусто — синк и tools поиска отключены, старый `read_drive_document` работает.

## Листы Google Sheets

### `knowledge_sources`

| Колонка | Тип | Описание |
|---------|-----|----------|
| `source_id` | UUID | Первичный ключ |
| `drive_file_id` | string | ID файла в Drive (пусто для URL-источников) |
| `title` | string | Имя файла |
| `mime_type` | string | MIME Drive |
| `content_hash` | string | SHA256 текста (для пропуска неизменённых) |
| `indexed_at` | datetime | Последняя индексация |
| `active` | bool | `true` / `false` |
| `chunk_count` | int | Число фрагментов |
| `error` | string | Последняя ошибка индексации |

### `knowledge_chunks`

| Колонка | Тип | Описание |
|---------|-----|----------|
| `source_id` | UUID | FK → knowledge_sources |
| `chunk_index` | int | 0..N |
| `text` | string | Фрагмент (до ~900 символов) |

Эмбеддинги хранятся на диске: `data/knowledge_embeddings/{source_id}.json` (не в Sheets — лимит ячеек).

## Cron

- **08:00** `sync_drive_knowledge()` в `src/scheduler/knowledge_sync.py`
- Регистрация в `src/scheduler/runner.py`
- Логика: `list_folder` → для каждого файла сравнить hash → переиндексировать при изменении
- После успеха: `memory_facts` employee=`_system_knowledge`

## Tools (агент)

| Tool | Назначение |
|------|------------|
| `search_knowledge` | Семантический поиск по индексу |
| `sync_knowledge_folder` | Ручной полный синк папки |
| `list_knowledge_sources` | Список проиндексированных файлов |
| `ingest_knowledge_url` | MVP: публичный URL → временный source (опц.) |

## Поддерживаемые форматы (MVP)

- Google Docs, Sheets (CSV export), Presentations (text)
- `text/plain`
- PDF — опционально позже (`pypdf`)

## Критерии приёмки

1. Файл в папке Drive → после 08:00 или `sync_knowledge_folder` появляется в `knowledge_sources`.
2. Вопрос «что в меню про X» → `search_knowledge` → ответ с цитатой из чанка.
3. Изменение Doc → при следующем синке обновляются чанки.

## Код

- `src/agent/knowledge/` — chunking, extract, embed, index, search
- `src/scheduler/knowledge_sync.py`
- Расширение `VALID_SHEETS` в `sheets.py`
