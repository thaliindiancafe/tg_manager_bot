"""Google Tasks access via OAuth (personal Gmail) or service account fallback."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from src.config import settings

from google.oauth2 import service_account
from googleapiclient.discovery import Resource, build

from src.google.oauth_credentials import (
    TASKS_SCOPES,
    load_user_credentials,
    oauth_configured,
)

logger = logging.getLogger(__name__)

SA_SCOPES = ["https://www.googleapis.com/auth/tasks"]

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_service: Resource | None = None
_service_mode: str | None = None


def _resolve_credentials_path() -> Path:
    path = Path(settings.google_credentials_json)
    if not path.is_absolute():
        path = _PROJECT_ROOT / path
    if not path.is_file():
        raise FileNotFoundError(f"Service account file not found: {path}")
    return path


def _build_service() -> Resource:
    global _service, _service_mode

    use_oauth = settings.google_tasks_use_oauth and oauth_configured()
    mode = "oauth" if use_oauth else "service_account"

    if _service is not None and _service_mode == mode:
        return _service

    if use_oauth:
        credentials = load_user_credentials(TASKS_SCOPES)
        logger.debug("Google Tasks API: OAuth user credentials")
    else:
        if settings.google_tasks_use_oauth and not oauth_configured():
            logger.warning(
                "Google Tasks OAuth enabled but token/client missing; "
                "Tasks API will use service account (personal lists won't work)"
            )
        credentials = service_account.Credentials.from_service_account_file(
            str(_resolve_credentials_path()),
            scopes=SA_SCOPES,
        )
        logger.debug("Google Tasks API: service account")

    _service = build("tasks", "v1", credentials=credentials, cache_discovery=False)
    _service_mode = mode
    return _service


def _validate_rfc3339(due: str) -> str:
    text = due.strip()
    if not text:
        raise ValueError("due must be a non-empty RFC 3339 datetime")

    normalized = text.replace("Z", "+00:00")
    try:
        datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError(
            f"due must be RFC 3339, e.g. 2026-05-14T00:00:00Z, got: {due!r}"
        ) from exc

    return text


def _task_to_dict(task: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": task.get("id", ""),
        "title": task.get("title", ""),
        "notes": task.get("notes", ""),
        "due": task.get("due", ""),
        "status": task.get("status", ""),
        "completed": task.get("completed", ""),
        "updated": task.get("updated", ""),
        "position": task.get("position", ""),
    }


def _tasklist_to_dict(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": item.get("id", ""),
        "title": item.get("title", ""),
        "updated": item.get("updated", ""),
    }


def _list_tasklists_sync() -> list[dict[str, Any]]:
    service = _build_service()
    items: list[dict[str, Any]] = []
    page_token: str | None = None

    while True:
        result = (
            service.tasklists()
            .list(maxResults=100, pageToken=page_token)
            .execute()
        )
        items.extend(result.get("items", []))
        page_token = result.get("nextPageToken")
        if not page_token:
            break

    return [_tasklist_to_dict(item) for item in items]


def _get_tasks_sync(tasklist_id: str) -> list[dict[str, Any]]:
    service = _build_service()
    items: list[dict[str, Any]] = []
    page_token: str | None = None

    while True:
        result = (
            service.tasks()
            .list(
                tasklist=tasklist_id,
                showCompleted=True,
                showHidden=False,
                pageToken=page_token,
            )
            .execute()
        )
        items.extend(result.get("items", []))
        page_token = result.get("nextPageToken")
        if not page_token:
            break

    return [_task_to_dict(task) for task in items]


def _create_task_sync(
    tasklist_id: str,
    title: str,
    due: str,
    notes: str,
) -> str:
    body = {
        "title": title,
        "notes": notes,
        "due": _validate_rfc3339(due),
    }
    service = _build_service()
    created = service.tasks().insert(tasklist=tasklist_id, body=body).execute()
    task_id = created.get("id")
    if not task_id:
        raise RuntimeError("Google Tasks did not return task id")
    return str(task_id)


def _complete_task_sync(tasklist_id: str, task_id: str) -> None:
    service = _build_service()
    (
        service.tasks()
        .patch(
            tasklist=tasklist_id,
            task=task_id,
            body={"status": "completed"},
        )
        .execute()
    )


def _update_task_deadline_sync(tasklist_id: str, task_id: str, new_due: str) -> None:
    service = _build_service()
    (
        service.tasks()
        .patch(
            tasklist=tasklist_id,
            task=task_id,
            body={"due": _validate_rfc3339(new_due)},
        )
        .execute()
    )


def _insert_tasklist_sync(title: str) -> dict[str, Any]:
    service = _build_service()
    result = (
        service.tasklists()
        .insert(body={"title": title.strip()})
        .execute()
    )
    return _tasklist_to_dict(result)


async def create_tasklist(title: str) -> dict[str, Any]:
    """Create a new Google Tasks list. Returns {id, title, updated}."""
    name = (title or "").strip()
    if not name:
        raise ValueError("tasklist title is required")
    try:
        return await asyncio.to_thread(_insert_tasklist_sync, name)
    except Exception as exc:
        logger.error(
            "create_tasklist failed: title=%s error=%s",
            name,
            exc,
            exc_info=True,
        )
        raise


async def list_tasklists() -> list[dict[str, Any]]:
    """All task lists in the connected Google account (OAuth recommended)."""
    try:
        return await asyncio.to_thread(_list_tasklists_sync)
    except Exception as exc:
        logger.error("list_tasklists failed: error=%s", exc, exc_info=True)
        raise


def _task_due_date_local(due: str | None) -> str | None:
    """Extract YYYY-MM-DD in app timezone from Google Tasks due (RFC 3339)."""
    if not due or not str(due).strip():
        return None
    text = str(due).strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=ZoneInfo(settings.timezone))
        return dt.astimezone(ZoneInfo(settings.timezone)).strftime("%Y-%m-%d")
    except ValueError:
        return text[:10] if len(text) >= 10 else None


def _list_open_tasks_for_dates_sync(dates: list[str]) -> list[dict[str, Any]]:
    target_dates = {d.strip()[:10] for d in dates if d.strip()}
    if not target_dates:
        return []

    service = _build_service()
    tasklists_result = (
        service.tasklists().list(maxResults=100).execute()
    )
    matched: list[dict[str, Any]] = []

    for tasklist in tasklists_result.get("items", []):
        tasklist_id = str(tasklist.get("id", "")).strip()
        tasklist_title = str(tasklist.get("title", "")).strip()
        if not tasklist_id:
            continue

        page_token: str | None = None
        while True:
            result = (
                service.tasks()
                .list(
                    tasklist=tasklist_id,
                    showCompleted=False,
                    showHidden=False,
                    pageToken=page_token,
                )
                .execute()
            )
            for raw in result.get("items", []):
                if str(raw.get("status", "")).strip().lower() == "completed":
                    continue
                due_day = _task_due_date_local(raw.get("due"))
                if due_day not in target_dates:
                    continue
                row = _task_to_dict(raw)
                row["due_date"] = due_day or ""
                row["tasklist_id"] = tasklist_id
                row["tasklist_title"] = tasklist_title
                matched.append(row)

            page_token = result.get("nextPageToken")
            if not page_token:
                break

    matched.sort(key=lambda r: (r.get("due_date", ""), r.get("tasklist_title", "")))
    return matched


async def list_open_tasks_for_dates(dates: list[str]) -> list[dict[str, Any]]:
    """Open Google Tasks with due date on any of the given YYYY-MM-DD days (all task lists)."""
    try:
        return await asyncio.to_thread(_list_open_tasks_for_dates_sync, dates)
    except Exception as exc:
        logger.error(
            "list_open_tasks_for_dates failed: dates=%s error=%s",
            dates,
            exc,
            exc_info=True,
        )
        raise


def _list_open_tasks_in_tasklist_sync(
    tasklist_id: str,
    tasklist_title: str,
    dates: list[str] | None,
    *,
    include_undated: bool = False,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """
    Open tasks in one list: due on given days, and optionally without due date.

    Returns (dated_rows, undated_rows).
    """
    if not tasklist_id:
        return [], []

    target_dates = {d.strip()[:10] for d in (dates or []) if d.strip()}
    if not target_dates and not include_undated:
        return [], []

    service = _build_service()
    dated: list[dict[str, Any]] = []
    undated: list[dict[str, Any]] = []
    page_token: str | None = None

    while True:
        result = (
            service.tasks()
            .list(
                tasklist=tasklist_id,
                showCompleted=False,
                showHidden=False,
                pageToken=page_token,
            )
            .execute()
        )
        for raw in result.get("items", []):
            if str(raw.get("status", "")).strip().lower() == "completed":
                continue
            due_day = _task_due_date_local(raw.get("due"))
            row = _task_to_dict(raw)
            row["due_date"] = due_day or ""
            row["tasklist_id"] = tasklist_id
            row["tasklist_title"] = tasklist_title
            if due_day and due_day in target_dates:
                dated.append(row)
            elif include_undated and not due_day:
                undated.append(row)

        page_token = result.get("nextPageToken")
        if not page_token:
            break

    dated.sort(key=lambda r: (r.get("due_date", ""), r.get("title", "")))
    undated.sort(key=lambda r: r.get("title", ""))
    return dated, undated


def _list_all_open_tasks_in_tasklist_sync(
    tasklist_id: str,
    tasklist_title: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """All open tasks in a list: (with due date, without due date)."""
    if not tasklist_id:
        return [], []

    service = _build_service()
    dated: list[dict[str, Any]] = []
    undated: list[dict[str, Any]] = []
    page_token: str | None = None

    while True:
        result = (
            service.tasks()
            .list(
                tasklist=tasklist_id,
                showCompleted=False,
                showHidden=False,
                pageToken=page_token,
            )
            .execute()
        )
        for raw in result.get("items", []):
            if str(raw.get("status", "")).strip().lower() == "completed":
                continue
            due_day = _task_due_date_local(raw.get("due"))
            row = _task_to_dict(raw)
            row["due_date"] = due_day or ""
            row["tasklist_id"] = tasklist_id
            row["tasklist_title"] = tasklist_title
            if due_day:
                dated.append(row)
            else:
                undated.append(row)

        page_token = result.get("nextPageToken")
        if not page_token:
            break

    dated.sort(key=lambda r: (r.get("due_date", ""), r.get("title", "")))
    undated.sort(key=lambda r: r.get("title", ""))
    return dated, undated


async def list_all_open_tasks_in_tasklist(
    tasklist_id: str,
    tasklist_title: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """All open tasks in one Google Tasks list."""
    try:
        return await asyncio.to_thread(
            _list_all_open_tasks_in_tasklist_sync,
            tasklist_id,
            tasklist_title,
        )
    except Exception as exc:
        logger.error(
            "list_all_open_tasks_in_tasklist failed: tasklist=%s error=%s",
            tasklist_id,
            exc,
            exc_info=True,
        )
        raise


def _list_open_tasks_in_tasklist_for_dates_sync(
    tasklist_id: str,
    tasklist_title: str,
    dates: list[str],
) -> list[dict[str, Any]]:
    dated, _ = _list_open_tasks_in_tasklist_sync(
        tasklist_id, tasklist_title, dates, include_undated=False
    )
    return dated


async def list_open_tasks_in_tasklist_for_dates(
    tasklist_id: str,
    tasklist_title: str,
    dates: list[str],
    *,
    include_undated: bool = False,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Open tasks in one Google Tasks list for given YYYY-MM-DD days (+ optional undated)."""
    try:
        return await asyncio.to_thread(
            _list_open_tasks_in_tasklist_sync,
            tasklist_id,
            tasklist_title,
            dates,
            include_undated=include_undated,
        )
    except Exception as exc:
        logger.error(
            "list_open_tasks_in_tasklist_for_dates failed: tasklist=%s dates=%s error=%s",
            tasklist_id,
            dates,
            exc,
            exc_info=True,
        )
        raise


async def list_open_tasks_in_tasklist_dated_only(
    tasklist_id: str,
    tasklist_title: str,
    dates: list[str],
) -> list[dict[str, Any]]:
    """Backward-compatible: only tasks with due on given days."""
    dated, _ = await list_open_tasks_in_tasklist_for_dates(
        tasklist_id, tasklist_title, dates, include_undated=False
    )
    return dated


async def get_tasks(tasklist_id: str) -> list[dict[str, Any]]:
    """Return all tasks from the task list (including completed)."""
    try:
        return await asyncio.to_thread(_get_tasks_sync, tasklist_id)
    except Exception as exc:
        logger.error(
            "get_tasks failed: tasklist_id=%s error=%s",
            tasklist_id,
            exc,
            exc_info=True,
        )
        raise


async def create_task(
    tasklist_id: str,
    title: str,
    due: str,
    notes: str,
) -> str:
    """Create a task. due must be RFC 3339 (e.g. 2026-05-14T00:00:00Z). Returns task_id."""
    try:
        return await asyncio.to_thread(
            _create_task_sync,
            tasklist_id,
            title,
            due,
            notes,
        )
    except Exception as exc:
        logger.error(
            "create_task failed: tasklist_id=%s title=%s due=%s error=%s",
            tasklist_id,
            title,
            due,
            exc,
            exc_info=True,
        )
        raise


async def complete_task(tasklist_id: str, task_id: str) -> None:
    """Mark a task as completed."""
    try:
        await asyncio.to_thread(_complete_task_sync, tasklist_id, task_id)
    except Exception as exc:
        logger.error(
            "complete_task failed: tasklist_id=%s task_id=%s error=%s",
            tasklist_id,
            task_id,
            exc,
            exc_info=True,
        )
        raise


def _patch_task_notes_sync(tasklist_id: str, task_id: str, notes: str) -> None:
    service = _build_service()
    (
        service.tasks()
        .patch(
            tasklist=tasklist_id,
            task=task_id,
            body={"notes": notes},
        )
        .execute()
    )


async def patch_task_notes(tasklist_id: str, task_id: str, notes: str) -> None:
    """Replace task notes (used for reminder markers)."""
    try:
        await asyncio.to_thread(
            _patch_task_notes_sync,
            tasklist_id,
            task_id,
            notes,
        )
    except Exception as exc:
        logger.error(
            "patch_task_notes failed: tasklist=%s task=%s error=%s",
            tasklist_id,
            task_id,
            exc,
            exc_info=True,
        )
        raise


async def update_task_deadline(
    tasklist_id: str,
    task_id: str,
    new_due: str,
) -> None:
    """Update task due date. new_due must be RFC 3339."""
    try:
        await asyncio.to_thread(
            _update_task_deadline_sync,
            tasklist_id,
            task_id,
            new_due,
        )
    except Exception as exc:
        logger.error(
            "update_task_deadline failed: tasklist_id=%s task_id=%s new_due=%s error=%s",
            tasklist_id,
            task_id,
            new_due,
            exc,
            exc_info=True,
        )
        raise
