"""Google Sheets access via service account (async wrappers)."""

from __future__ import annotations

import asyncio
import logging
import ssl
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, TypeVar
from zoneinfo import ZoneInfo

from google.oauth2 import service_account
from googleapiclient.discovery import Resource, build
from googleapiclient.errors import HttpError

from src.config import settings
from src.google.http_transport import build_authorized_http

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
CACHE_TTL_SECONDS = 60
BACKOFF_DELAYS = (1, 2, 4, 8, 16)

VALID_SHEETS = frozenset(
    {
        "schedule",
        "tasks",
        "events",
        "chats",
        "employees",
        "automations",
        "memory_facts",
        "memory_history",
        "knowledge_sources",
        "knowledge_chunks",
    }
)

SHEET_HEADERS: dict[str, list[str]] = {
    "schedule": [
        "date",
        "employee",
        "role",
        "shift_start",
        "shift_end",
        "telegram_user_id",
    ],
    "tasks": [
        "task_id",
        "title",
        "assigned_to",
        "due_date",
        "status",
        "reminder_count",
        "notes",
    ],
    "events": [
        "title",
        "date",
        "time",
        "description",
        "calendar_id",
        "created_by",
    ],
    "chats": ["chat_id", "chat_name", "active", "timezone"],
    "employees": [
        "name",
        "telegram_user_id",
        "username",
        "role",
        "google_tasks_id",
        "active",
    ],
    "automations": [
        "id",
        "trigger_type",
        "trigger_time",
        "trigger_day",
        "action",
        "params",
        "active",
    ],
    "memory_facts": ["employee", "fact", "created_at"],
    "memory_history": ["chat_id", "role", "content", "timestamp"],
    "knowledge_sources": [
        "source_id",
        "drive_file_id",
        "title",
        "mime_type",
        "content_hash",
        "indexed_at",
        "active",
        "chunk_count",
        "error",
    ],
    "knowledge_chunks": [
        "source_id",
        "chunk_index",
        "text",
    ],
}

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
T = TypeVar("T")

_service: Resource | None = None
_credentials: service_account.Credentials | None = None
_sheet_ids: dict[str, int] = {}
_sheet_cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}
_cache_lock = asyncio.Lock()
_sheets_semaphore: asyncio.Semaphore | None = None


def _resolve_credentials_path() -> Path:
    path = Path(settings.google_credentials_json)
    if not path.is_absolute():
        path = _PROJECT_ROOT / path
    if not path.is_file():
        raise FileNotFoundError(f"Service account file not found: {path}")
    return path


def _reset_sheets_service() -> None:
    global _service
    _service = None


def _get_sheets_semaphore() -> asyncio.Semaphore:
    global _sheets_semaphore
    if _sheets_semaphore is None:
        _sheets_semaphore = asyncio.Semaphore(settings.google_sheets_max_concurrent)
    return _sheets_semaphore


def _build_service() -> Resource:
    global _service, _credentials
    if _service is not None:
        return _service

    _credentials = service_account.Credentials.from_service_account_file(
        str(_resolve_credentials_path()),
        scopes=SCOPES,
    )
    http = build_authorized_http(_credentials)
    _service = build("sheets", "v4", http=http, cache_discovery=False)
    return _service


def _validate_sheet_name(sheet_name: str) -> None:
    if sheet_name not in VALID_SHEETS:
        raise ValueError(
            f"Unknown sheet '{sheet_name}'. Allowed: {', '.join(sorted(VALID_SHEETS))}"
        )


def _is_rate_limit_error(exc: BaseException) -> bool:
    if isinstance(exc, HttpError) and exc.resp is not None:
        return exc.resp.status == 429
    return False


def _is_transient_sheets_error(exc: BaseException) -> bool:
    if _is_rate_limit_error(exc):
        return True
    if isinstance(exc, (ssl.SSLError, ConnectionError, TimeoutError, OSError)):
        return True
    if isinstance(exc, asyncio.TimeoutError):
        return True
    if isinstance(exc, HttpError) and exc.resp is not None:
        return exc.resp.status in {500, 502, 503, 504}
    return False


def _should_reset_connection(exc: BaseException) -> bool:
    return isinstance(
        exc,
        (ssl.SSLError, ConnectionError, TimeoutError, OSError, asyncio.TimeoutError),
    )


async def _execute_with_backoff(
    func: Callable[..., T],
    *args: Any,
    **kwargs: Any,
) -> T:
    max_attempts = settings.google_sheets_max_attempts
    timeout_sec = settings.google_sheets_request_timeout
    last_error: BaseException | None = None

    async with _get_sheets_semaphore():
        for attempt in range(max_attempts):
            try:
                return await asyncio.wait_for(
                    asyncio.to_thread(func, *args, **kwargs),
                    timeout=timeout_sec,
                )
            except asyncio.TimeoutError:
                last_error = TimeoutError(
                    f"Google Sheets: нет ответа за {timeout_sec} с "
                    f"(попытка {attempt + 1}/{max_attempts})"
                )
                _reset_sheets_service()
            except Exception as exc:
                last_error = exc
                if _should_reset_connection(exc):
                    _reset_sheets_service()

            if last_error is None:
                continue

            if _is_transient_sheets_error(last_error) and attempt < max_attempts - 1:
                delay = BACKOFF_DELAYS[min(attempt, len(BACKOFF_DELAYS) - 1)]
                logger.warning(
                    "Google Sheets transient error (%s), retry %s/%s in %ss",
                    type(last_error).__name__,
                    attempt + 1,
                    max_attempts,
                    delay,
                )
                await asyncio.sleep(delay)
                continue
            raise last_error

    assert last_error is not None
    raise last_error


async def _invalidate_cache(sheet_name: str) -> None:
    async with _cache_lock:
        _sheet_cache.pop(sheet_name, None)


def _col_letter(index: int) -> str:
    """Convert 1-based column index to A1 notation letter(s)."""
    result = ""
    while index > 0:
        index, remainder = divmod(index - 1, 26)
        result = chr(65 + remainder) + result
    return result


def _rows_to_dicts(values: list[list[Any]]) -> list[dict[str, Any]]:
    if not values:
        return []

    headers = [str(h).strip() for h in values[0]]
    rows: list[dict[str, Any]] = []

    for row_values in values[1:]:
        row: dict[str, Any] = {}
        for idx, header in enumerate(headers):
            if not header:
                continue
            row[header] = row_values[idx] if idx < len(row_values) else ""
        if row:
            rows.append(row)

    return rows


def _get_headers_sync(sheet_name: str) -> list[str]:
    service = _build_service()
    result = (
        service.spreadsheets()
        .values()
        .get(
            spreadsheetId=settings.spreadsheet_id,
            range=f"{sheet_name}!1:1",
        )
        .execute()
    )
    values = result.get("values", [])
    if not values:
        return []
    return [str(h).strip() for h in values[0]]


def _read_sheet_sync(sheet_name: str) -> list[dict[str, Any]]:
    service = _build_service()
    result = (
        service.spreadsheets()
        .values()
        .get(
            spreadsheetId=settings.spreadsheet_id,
            range=f"{sheet_name}!A:Z",
        )
        .execute()
    )
    return _rows_to_dicts(result.get("values", []))


def _get_sheet_id_sync(sheet_name: str) -> int:
    if sheet_name in _sheet_ids:
        return _sheet_ids[sheet_name]

    service = _build_service()
    meta = (
        service.spreadsheets()
        .get(spreadsheetId=settings.spreadsheet_id, fields="sheets.properties")
        .execute()
    )

    for sheet in meta.get("sheets", []):
        props = sheet.get("properties", {})
        if props.get("title") == sheet_name:
            sheet_id = int(props["sheetId"])
            _sheet_ids[sheet_name] = sheet_id
            return sheet_id

    raise ValueError(f"Sheet tab '{sheet_name}' not found in spreadsheet")


def _append_row_sync(sheet_name: str, row: dict[str, Any]) -> None:
    headers = _get_headers_sync(sheet_name)
    if not headers:
        raise ValueError(f"Sheet '{sheet_name}' has no header row")

    values = [row.get(header, "") for header in headers]
    service = _build_service()
    (
        service.spreadsheets()
        .values()
        .append(
            spreadsheetId=settings.spreadsheet_id,
            range=f"{sheet_name}!A:{_col_letter(len(headers))}",
            valueInputOption="USER_ENTERED",
            insertDataOption="INSERT_ROWS",
            body={"values": [values]},
        )
        .execute()
    )


def _update_row_sync(sheet_name: str, row_index: int, row: dict[str, Any]) -> None:
    if row_index < 2:
        raise ValueError("row_index must be >= 2 (row 1 is the header)")

    headers = _get_headers_sync(sheet_name)
    if not headers:
        raise ValueError(f"Sheet '{sheet_name}' has no header row")

    values = [row.get(header, "") for header in headers]
    end_col = _col_letter(len(headers))
    service = _build_service()
    (
        service.spreadsheets()
        .values()
        .update(
            spreadsheetId=settings.spreadsheet_id,
            range=f"{sheet_name}!A{row_index}:{end_col}{row_index}",
            valueInputOption="USER_ENTERED",
            body={"values": [values]},
        )
        .execute()
    )


def _delete_row_sync(sheet_name: str, row_index: int) -> None:
    if row_index < 2:
        raise ValueError("row_index must be >= 2 (row 1 is the header)")

    sheet_id = _get_sheet_id_sync(sheet_name)
    service = _build_service()
    (
        service.spreadsheets()
        .batchUpdate(
            spreadsheetId=settings.spreadsheet_id,
            body={
                "requests": [
                    {
                        "deleteDimension": {
                            "range": {
                                "sheetId": sheet_id,
                                "dimension": "ROWS",
                                "startIndex": row_index - 1,
                                "endIndex": row_index,
                            }
                        }
                    }
                ]
            },
        )
        .execute()
    )


def _parse_timestamp(value: Any) -> datetime | None:
    if value is None or value == "":
        return None

    text = str(value).strip()
    if not text:
        return None

    for fmt in (
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
        "%d.%m.%Y %H:%M:%S",
        "%d.%m.%Y",
        "%Y-%m-%d",
    ):
        try:
            parsed = datetime.strptime(text.replace("Z", "+0000"), fmt)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed
        except ValueError:
            continue

    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    except ValueError:
        logger.warning("Could not parse timestamp: %s", text)
        return None


async def read_sheet(sheet_name: str) -> list[dict[str, Any]]:
    """Read all rows from a sheet as list of dicts (header row = keys). Cached 60s."""
    try:
        _validate_sheet_name(sheet_name)
        now = time.monotonic()

        async with _cache_lock:
            cached = _sheet_cache.get(sheet_name)
            if cached and now - cached[0] < CACHE_TTL_SECONDS:
                return list(cached[1])

        rows = await _execute_with_backoff(_read_sheet_sync, sheet_name)

        async with _cache_lock:
            _sheet_cache[sheet_name] = (now, rows)

        return list(rows)
    except Exception as exc:
        async with _cache_lock:
            stale = _sheet_cache.get(sheet_name)
            if stale:
                logger.warning(
                    "read_sheet: stale cache for %s after %s",
                    sheet_name,
                    exc,
                )
                return list(stale[1])
        logger.error(
            "read_sheet failed: sheet=%s error=%s",
            sheet_name,
            exc,
            exc_info=True,
        )
        raise


async def append_row(sheet_name: str, row: dict[str, Any]) -> None:
    """Append a row to the sheet (values aligned to header columns)."""
    try:
        _validate_sheet_name(sheet_name)
        await _execute_with_backoff(_append_row_sync, sheet_name, row)
        await _invalidate_cache(sheet_name)
    except Exception as exc:
        logger.error(
            "append_row failed: sheet=%s row=%s error=%s",
            sheet_name,
            row,
            exc,
            exc_info=True,
        )
        raise


async def update_row(sheet_name: str, row_index: int, row: dict[str, Any]) -> None:
    """Update a row by 1-based spreadsheet row number (row 1 = header)."""
    try:
        _validate_sheet_name(sheet_name)
        await _execute_with_backoff(_update_row_sync, sheet_name, row_index, row)
        await _invalidate_cache(sheet_name)
    except Exception as exc:
        logger.error(
            "update_row failed: sheet=%s row_index=%s row=%s error=%s",
            sheet_name,
            row_index,
            row,
            exc,
            exc_info=True,
        )
        raise


async def find_row(sheet_name: str, column: str, value: str) -> dict[str, Any] | None:
    """Return the first row where column equals value, or None."""
    try:
        _validate_sheet_name(sheet_name)
        rows = await read_sheet(sheet_name)
        target = str(value)
        for row in rows:
            if str(row.get(column, "")) == target:
                return row
        return None
    except Exception as exc:
        logger.error(
            "find_row failed: sheet=%s column=%s value=%s error=%s",
            sheet_name,
            column,
            value,
            exc,
            exc_info=True,
        )
        raise


async def delete_row(sheet_name: str, row_index: int) -> None:
    """Delete a row by 1-based spreadsheet row number (row 1 = header)."""
    try:
        _validate_sheet_name(sheet_name)
        await _execute_with_backoff(_delete_row_sync, sheet_name, row_index)
        await _invalidate_cache(sheet_name)
    except Exception as exc:
        logger.error(
            "delete_row failed: sheet=%s row_index=%s error=%s",
            sheet_name,
            row_index,
            exc,
            exc_info=True,
        )
        raise


def _storage_is_db() -> bool:
    return (getattr(settings, "storage_backend", "sheets") or "sheets").strip().lower() == "db"


async def get_recent_history(chat_id: int | str, limit: int = 30) -> list[dict[str, Any]]:
    """Last N memory_history rows for chat_id, newest first."""
    try:
        if _storage_is_db():
            from src.storage import get_store

            return await get_store().memory.get_recent_history(int(chat_id), limit=limit)
        chat_key = str(chat_id)
        rows = await read_sheet("memory_history")
        matched = [row for row in rows if str(row.get("chat_id", "")) == chat_key]

        matched.sort(
            key=lambda row: _parse_timestamp(row.get("timestamp")) or datetime.min.replace(
                tzinfo=timezone.utc
            ),
            reverse=True,
        )
        return matched[:limit]
    except Exception as exc:
        logger.warning(
            "get_recent_history failed (empty history): chat_id=%s error=%s",
            chat_id,
            exc,
        )
        return []


async def get_chat_labels_map() -> dict[str, str]:
    """Map telegram chat_id (string) -> human label from sheet chats."""
    try:
        if _storage_is_db():
            from src.storage.access import list_chats

            rows = await list_chats()
        else:
            rows = await read_sheet("chats")
        out: dict[str, str] = {}
        for row in rows:
            cid = str(row.get("chat_id", "")).strip()
            if not cid:
                continue
            name = str(row.get("chat_name", "")).strip()
            out[cid] = name or f"chat {cid}"
        return out
    except Exception as exc:
        logger.error("get_chat_labels_map failed: error=%s", exc, exc_info=True)
        raise


async def get_recent_history_other_chats(
    exclude_chat_id: int | str,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """
    Last N memory_history rows from all chats except exclude_chat_id, newest first.

    Used for cross-chat context (Mira-style shared memory across Telegram chats).
    """
    try:
        exclude = str(exclude_chat_id).strip()
        if limit < 1:
            return []

        if _storage_is_db():
            from src.storage import get_store

            return await get_store().memory.get_recent_history_other_chats(
                exclude_chat_id=int(exclude_chat_id),
                limit=limit,
            )

        rows = await read_sheet("memory_history")
        matched = [
            row
            for row in rows
            if str(row.get("chat_id", "")).strip() != exclude
        ]

        matched.sort(
            key=lambda row: _parse_timestamp(row.get("timestamp"))
            or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )
        return matched[:limit]
    except Exception as exc:
        logger.warning(
            "get_recent_history_other_chats failed (skipped): exclude=%s error=%s",
            exclude_chat_id,
            exc,
        )
        return []


async def get_facts() -> list[dict[str, Any]]:
    """All rows from memory_facts."""
    try:
        if _storage_is_db():
            from src.storage import get_store

            return await get_store().memory.list_facts()
        return await read_sheet("memory_facts")
    except Exception as exc:
        logger.warning("get_facts failed (empty facts): error=%s", exc)
        return []


def _list_sheet_titles_sync() -> set[str]:
    service = _build_service()
    meta = (
        service.spreadsheets()
        .get(spreadsheetId=settings.spreadsheet_id, fields="sheets.properties.title")
        .execute()
    )
    return {
        sheet["properties"]["title"]
        for sheet in meta.get("sheets", [])
        if sheet.get("properties", {}).get("title")
    }


def _ensure_sheets_exist_sync() -> list[str]:
    existing = _list_sheet_titles_sync()
    missing = sorted(VALID_SHEETS - existing)
    if not missing:
        return []

    service = _build_service()
    (
        service.spreadsheets()
        .batchUpdate(
            spreadsheetId=settings.spreadsheet_id,
            body={
                "requests": [
                    {"addSheet": {"properties": {"title": name}}} for name in missing
                ]
            },
        )
        .execute()
    )

    _sheet_ids.clear()
    return missing


def _init_sheet_header_sync(sheet_name: str, headers: list[str], force: bool) -> str:
    current = _get_headers_sync(sheet_name)

    if current == headers:
        return "ok"

    if current and not force:
        return "skipped"

    end_col = _col_letter(len(headers))
    service = _build_service()
    (
        service.spreadsheets()
        .values()
        .update(
            spreadsheetId=settings.spreadsheet_id,
            range=f"{sheet_name}!A1:{end_col}1",
            valueInputOption="RAW",
            body={"values": [headers]},
        )
        .execute()
    )
    return "updated" if current else "created"


async def init_all_sheets(force: bool = False) -> dict[str, str]:
    """
    Create missing sheet tabs and write header row (row 1) for all 8 sheets.

    Returns status per sheet: created | updated | ok | skipped.
    Use force=True to overwrite existing headers in row 1.
    """
    try:
        created_tabs = await _execute_with_backoff(_ensure_sheets_exist_sync)
        if created_tabs:
            logger.info("Created sheet tabs: %s", ", ".join(created_tabs))

        results: dict[str, str] = {}
        for sheet_name in sorted(VALID_SHEETS):
            headers = SHEET_HEADERS[sheet_name]
            status = await _execute_with_backoff(
                _init_sheet_header_sync, sheet_name, headers, force
            )
            if sheet_name in created_tabs and status == "created":
                status = "created"
            results[sheet_name] = status
            await _invalidate_cache(sheet_name)

        return results
    except Exception as exc:
        logger.error("init_all_sheets failed: error=%s", exc, exc_info=True)
        raise


def _source_sheet_a1_range(sheet_title: str, cell_range: str = "A:ZZ") -> str:
    escaped = str(sheet_title).replace("'", "''")
    return f"'{escaped}'!{cell_range}"


def _read_source_values_sync(sheet_title: str) -> list[list[Any]]:
    service = _build_service()
    result = (
        service.spreadsheets()
        .values()
        .get(
            spreadsheetId=settings.source_spreadsheet_id,
            range=_source_sheet_a1_range(sheet_title),
        )
        .execute()
    )
    return result.get("values", [])


def _list_source_sheet_titles_sync() -> list[str]:
    service = _build_service()
    meta = (
        service.spreadsheets()
        .get(spreadsheetId=settings.source_spreadsheet_id, fields="sheets.properties.title")
        .execute()
    )
    titles: list[str] = []
    for sheet in meta.get("sheets") or []:
        t = (sheet.get("properties") or {}).get("title")
        if t is not None:
            titles.append(str(t))
    return titles


def _replace_schedule_rows_sync(rows: list[dict[str, Any]]) -> None:
    headers = SHEET_HEADERS["schedule"]
    service = _build_service()
    (
        service.spreadsheets()
        .values()
        .clear(
            spreadsheetId=settings.spreadsheet_id,
            range="schedule!A2:Z",
        )
        .execute()
    )
    if not rows:
        return
    body_values = [[row.get(h, "") for h in headers] for row in rows]
    end_col = _col_letter(len(headers))
    end_row = len(body_values) + 1
    (
        service.spreadsheets()
        .values()
        .update(
            spreadsheetId=settings.spreadsheet_id,
            range=f"schedule!A2:{end_col}{end_row}",
            valueInputOption="USER_ENTERED",
            body={"values": body_values},
        )
        .execute()
    )


async def read_source_sheet_values(sheet_title: str) -> list[list[Any]]:
    """Read raw cell grid from a tab in SOURCE_SPREADSHEET_ID (not cached)."""
    try:
        return await _execute_with_backoff(_read_source_values_sync, sheet_title)
    except Exception as exc:
        logger.error(
            "read_source_sheet_values failed: sheet_title=%s error=%s",
            sheet_title,
            exc,
            exc_info=True,
        )
        raise


async def list_source_sheet_titles() -> list[str]:
    """Tab titles in SOURCE_SPREADSHEET_ID (order as in the spreadsheet)."""
    try:
        return await _execute_with_backoff(_list_source_sheet_titles_sync)
    except Exception as exc:
        logger.error("list_source_sheet_titles failed: error=%s", exc, exc_info=True)
        raise


async def replace_schedule_rows(rows: list[dict[str, Any]]) -> None:
    """Clear schedule data rows (from row 2) and write new rows; keeps header row 1."""
    try:
        _validate_sheet_name("schedule")
        await _execute_with_backoff(_replace_schedule_rows_sync, rows)
        await _invalidate_cache("schedule")
    except Exception as exc:
        logger.error(
            "replace_schedule_rows failed: rows=%s error=%s",
            len(rows),
            exc,
            exc_info=True,
        )
        raise


def _replace_events_rows_sync(rows: list[dict[str, Any]]) -> None:
    headers = SHEET_HEADERS["events"]
    service = _build_service()
    (
        service.spreadsheets()
        .values()
        .clear(
            spreadsheetId=settings.spreadsheet_id,
            range="events!A2:Z",
        )
        .execute()
    )
    if not rows:
        return
    body_values = [[row.get(h, "") for h in headers] for row in rows]
    end_col = _col_letter(len(headers))
    end_row = len(body_values) + 1
    (
        service.spreadsheets()
        .values()
        .update(
            spreadsheetId=settings.spreadsheet_id,
            range=f"events!A2:{end_col}{end_row}",
            valueInputOption="USER_ENTERED",
            body={"values": body_values},
        )
        .execute()
    )


async def replace_events_rows(rows: list[dict[str, Any]]) -> None:
    """Clear events data rows (from row 2) and write new rows; keeps header row 1."""
    try:
        _validate_sheet_name("events")
        await _execute_with_backoff(_replace_events_rows_sync, rows)
        await _invalidate_cache("events")
    except Exception as exc:
        logger.error(
            "replace_events_rows failed: rows=%s error=%s",
            len(rows),
            exc,
            exc_info=True,
        )
        raise


async def upsert_knowledge_source_row(
    source_id: str,
    row: dict[str, Any],
    existing: dict[str, Any] | None = None,
) -> None:
    """Insert or update knowledge_sources row by source_id."""
    try:
        if _storage_is_db():
            from src.storage.access import upsert_knowledge_source_row as _db_upsert

            await _db_upsert(source_id, row, existing)
            return
        _validate_sheet_name("knowledge_sources")
        sid = str(source_id).strip()
        if existing is not None:
            rows = await read_sheet("knowledge_sources")
            for offset, r in enumerate(rows):
                if str(r.get("source_id", "")).strip() == sid:
                    await update_row("knowledge_sources", offset + 2, row)
                    return
        rows = await read_sheet("knowledge_sources")
        for offset, r in enumerate(rows):
            if str(r.get("source_id", "")).strip() == sid:
                await update_row("knowledge_sources", offset + 2, row)
                return
        await append_row("knowledge_sources", row)
    except Exception as exc:
        logger.error(
            "upsert_knowledge_source_row failed: source_id=%s error=%s",
            source_id,
            exc,
            exc_info=True,
        )
        raise


async def upsert_memory_fact_row(employee_key: str, fact_text: str) -> None:
    """Insert or update one row in memory_facts matched by employee column."""
    try:
        if _storage_is_db():
            from src.storage import get_store

            await get_store().memory.upsert_fact(
                employee=str(employee_key).strip(),
                fact=fact_text,
            )
            return
        _validate_sheet_name("memory_facts")
        key = str(employee_key).strip()
        ts = datetime.now(ZoneInfo(settings.timezone)).strftime("%Y-%m-%d %H:%M:%S")
        rows = await read_sheet("memory_facts")
        for offset, row in enumerate(rows):
            if str(row.get("employee", "")).strip() == key:
                idx = offset + 2
                merged = dict(row)
                merged["fact"] = fact_text
                merged["created_at"] = ts
                await update_row("memory_facts", idx, merged)
                return
        await append_row(
            "memory_facts",
            {"employee": key, "fact": fact_text, "created_at": ts},
        )
    except Exception as exc:
        logger.error(
            "upsert_memory_fact_row failed: employee_key=%s error=%s",
            employee_key,
            exc,
            exc_info=True,
        )
        raise


async def cleanup_old_history(days: int = 30) -> int:
    """Delete memory_history rows older than `days`. Returns number of deleted rows."""
    try:
        rows = await read_sheet("memory_history")
        if not rows:
            return 0

        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        to_delete: list[int] = []

        for offset, row in enumerate(rows):
            parsed = _parse_timestamp(row.get("timestamp"))
            if parsed is not None and parsed < cutoff:
                to_delete.append(offset + 2)

        deleted = 0
        for row_index in sorted(to_delete, reverse=True):
            await delete_row("memory_history", row_index)
            deleted += 1

        return deleted
    except Exception as exc:
        logger.error(
            "cleanup_old_history failed: days=%s error=%s",
            days,
            exc,
            exc_info=True,
        )
        raise
