"""Import open Google Tasks into Sheets ``tasks`` (unified journal)."""

from __future__ import annotations

import logging
import re
import uuid
from typing import Any

from src.agent.task_status import (
    TASK_STATUS_AWAITING_PROOF,
    TASK_STATUS_DONE,
    TASK_STATUS_REVIEW,
    is_closed_status,
    normalize_status,
)
from src.config import settings
from src.google import sheets
from src.google import tasks as google_tasks
from src.google.oauth_credentials import oauth_configured
from src.google.tasklist_resolve import resolve_tasklist_for_employee_row

logger = logging.getLogger(__name__)

_GT_IMPORT_MARKER = "__GT_SHEETS_IMPORT__:v1"
_GOOGLE_TASK_ID_RE = re.compile(r"google_task_id:([^\s\n]+)")


def _is_active_employee(row: dict[str, Any]) -> bool:
    return str(row.get("active", "true")).strip().lower() not in {
        "false",
        "0",
        "no",
        "нет",
        "off",
    }


def _extract_google_task_id(notes: str) -> str | None:
    m = _GOOGLE_TASK_ID_RE.search(notes or "")
    return m.group(1).strip() if m else None


def _merge_import_notes(
    existing_notes: str,
    google_task_id: str,
    tasklist_id: str,
    google_notes: str,
) -> str:
    """Preserve bot markers; ensure google_task_id / import marker present."""
    parts: list[str] = []
    base = (existing_notes or "").strip()
    if base:
        parts.append(base)
    if google_notes.strip() and google_notes.strip() not in base:
        parts.append(google_notes.strip())
    block = (
        f"google_task_id:{google_task_id}\n"
        f"tasklist_id:{tasklist_id}\n"
        f"{_GT_IMPORT_MARKER}"
    )
    if block not in "\n".join(parts):
        parts.append(block)
    return "\n\n".join(p for p in parts if p).strip()


def _index_tasks_by_google_id(
    rows: list[dict[str, Any]],
) -> dict[str, tuple[int, dict[str, Any]]]:
    """google_task_id -> (1-based sheet row index, row dict)."""
    out: dict[str, tuple[int, dict[str, Any]]] = {}
    for offset, row in enumerate(rows):
        gt_id = _extract_google_task_id(str(row.get("notes", "")))
        if gt_id:
            out[gt_id] = (offset + 2, row)
    return out


def _sheet_status_from_google(gt_status: str, current_sheet_status: str) -> str:
    if str(gt_status or "").strip().lower() == "completed":
        return TASK_STATUS_DONE
    cur = normalize_status(current_sheet_status)
    if is_closed_status(current_sheet_status):
        return str(current_sheet_status).strip() or TASK_STATUS_DONE
    if cur in {TASK_STATUS_AWAITING_PROOF, TASK_STATUS_REVIEW}:
        return str(current_sheet_status).strip()
    if cur:
        return cur
    return "pending"


async def sync_google_tasks_to_sheets() -> dict[str, int]:
    """
    Pull open tasks from each employee's Google Tasks list into ``tasks``.

    - New tasks in Google → new row (with __GT_SHEETS_IMPORT__).
    - Existing row with same google_task_id → update title, due_date, status.
    - Bot-created rows with google_task_id: also updated from Google.
    """
    stats = {"lists": 0, "created": 0, "updated": 0, "errors": 0}

    if not settings.google_tasks_sheets_sync_enabled:
        return stats
    if not settings.google_tasks_use_oauth or not oauth_configured():
        logger.debug("google_tasks_sheets_sync skipped: OAuth not configured")
        return stats

    try:
        sheet_rows = await sheets.read_sheet("tasks")
        employees = await sheets.read_sheet("employees")
    except Exception as exc:
        logger.error("google_tasks_sheets_sync: read sheets failed: %s", exc, exc_info=True)
        stats["errors"] += 1
        return stats

    by_gt_id = _index_tasks_by_google_id(sheet_rows)

    for emp in employees:
        if not _is_active_employee(emp):
            continue

        name = str(emp.get("name", "")).strip()
        if not name:
            continue

        resolved = await resolve_tasklist_for_employee_row(emp)
        if not resolved:
            continue

        tasklist_id, tasklist_title = resolved
        stats["lists"] += 1

        try:
            dated, undated = await google_tasks.list_all_open_tasks_in_tasklist(
                tasklist_id,
                tasklist_title or name,
            )
            open_tasks = dated + undated
        except Exception as exc:
            logger.warning(
                "google_tasks_sheets_sync: list failed employee=%s list=%s error=%s",
                name,
                tasklist_id,
                exc,
            )
            stats["errors"] += 1
            continue

        for gt in open_tasks:
            gt_id = str(gt.get("id", "")).strip()
            if not gt_id:
                continue

            title = str(gt.get("title", "")).strip() or "(без названия)"
            due_date = str(gt.get("due_date", "")).strip()[:10]
            gt_notes = str(gt.get("notes", "")).strip()
            gt_status = str(gt.get("status", "")).strip()

            if gt_id in by_gt_id:
                row_index, existing = by_gt_id[gt_id]
                new_status = _sheet_status_from_google(
                    gt_status, str(existing.get("status", ""))
                )
                updated = dict(existing)
                updated["title"] = title
                if due_date:
                    updated["due_date"] = due_date
                updated["status"] = new_status
                assigned = str(existing.get("assigned_to", "")).strip()
                if not assigned or assigned != name:
                    updated["assigned_to"] = name
                updated["notes"] = _merge_import_notes(
                    str(existing.get("notes", "")),
                    gt_id,
                    tasklist_id,
                    gt_notes,
                )
                try:
                    await sheets.update_row("tasks", row_index, updated)
                    by_gt_id[gt_id] = (row_index, updated)
                    stats["updated"] += 1
                except Exception as exc:
                    logger.warning(
                        "google_tasks_sheets_sync: update failed gt_id=%s error=%s",
                        gt_id,
                        exc,
                    )
                    stats["errors"] += 1
                continue

            notes = _merge_import_notes("", gt_id, tasklist_id, gt_notes)
            new_row = {
                "task_id": str(uuid.uuid4()),
                "title": title,
                "assigned_to": name,
                "due_date": due_date,
                "status": _sheet_status_from_google(gt_status, "pending"),
                "reminder_count": "0",
                "notes": notes,
            }
            try:
                await sheets.append_row("tasks", new_row)
                stats["created"] += 1
                by_gt_id[gt_id] = (-1, new_row)  # row index unknown until next read
            except Exception as exc:
                logger.warning(
                    "google_tasks_sheets_sync: append failed gt_id=%s error=%s",
                    gt_id,
                    exc,
                )
                stats["errors"] += 1

    if stats["created"] or stats["updated"]:
        logger.info(
            "google_tasks_sheets_sync: lists=%s created=%s updated=%s errors=%s",
            stats["lists"],
            stats["created"],
            stats["updated"],
            stats["errors"],
        )
    return stats
