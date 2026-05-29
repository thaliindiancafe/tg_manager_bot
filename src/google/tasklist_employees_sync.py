"""Link Google Tasks lists ↔ employees.google_tasks_id; optional new rows from lists."""

from __future__ import annotations

import logging
from typing import Any

from src.config import settings
from src.google import sheets
from src.google import tasks as google_tasks
from src.storage.access import (
    link_employee_google_tasks_id,
    list_employees,
    upsert_employee_row,
)
from src.google.oauth_credentials import oauth_configured
from src.google.tasklist_resolve import (
    _normalize_title,
    is_manager_employee_row,
    match_tasklist_for_employee_name,
)

logger = logging.getLogger(__name__)

_SKIP_LIST_TITLES = frozenset({"мои задачи", "my tasks"})


def _is_skip_list_title(title: str) -> bool:
    return _normalize_title(title) in _SKIP_LIST_TITLES


def _list_id_in_employees(rows: list[dict[str, Any]], list_id: str) -> bool:
    target = list_id.strip()
    return any(str(r.get("google_tasks_id", "")).strip() == target for r in rows)


def _employee_matches_list_title(
    rows: list[dict[str, Any]],
    list_title: str,
) -> bool:
    """True if some employees.name already matches this Tasks list title."""
    tl = {"id": "", "title": list_title}
    for row in rows:
        name = str(row.get("name", "")).strip()
        if not name:
            continue
        if match_tasklist_for_employee_name(name, [tl]):
            return True
    return False


async def sync_tasklists_to_employees_sheet(
    *,
    apply: bool = True,
    auto_register_from_lists: bool = True,
) -> dict[str, Any]:
    """
    Match OAuth task lists to employees rows; update google_tasks_id when changed.

    When auto_register_from_lists: append employees row for Tasks lists with no
    matching employee (name = list title). Does not duplicate rows if list id or
    name already linked.
    """
    if not settings.google_tasks_use_oauth or not oauth_configured():
        logger.debug("sync_tasklists_to_employees: OAuth not configured, skip")
        return {"ok": False, "skipped": True, "reason": "oauth_not_configured"}

    try:
        tasklists = await google_tasks.list_tasklists()
    except Exception as exc:
        logger.error("sync_tasklists_to_employees: list_tasklists failed: %s", exc, exc_info=True)
        return {"ok": False, "error": str(exc)}

    if not tasklists:
        logger.warning("sync_tasklists_to_employees: no task lists in OAuth account")
        return {"ok": True, "updated": 0, "created": 0, "tasklists": 0}

    employees = await list_employees()
    available = [
        tl
        for tl in tasklists
        if not _is_skip_list_title(str(tl.get("title", "")))
    ]

    updated = 0
    unmatched: list[str] = []
    used_list_ids: set[str] = set()

    for offset, row in enumerate(employees):
        name = str(row.get("name", "")).strip()
        if not name:
            continue

        match = match_tasklist_for_employee_name(name, available)
        if match is None:
            unmatched.append(name)
            continue

        list_id = str(match.get("id", "")).strip()
        list_title = str(match.get("title", "")).strip()
        if not list_id:
            continue

        if list_id in used_list_ids:
            logger.warning(
                "sync_tasklists_to_employees: list %r already assigned, skip employee %r",
                list_title,
                name,
            )
            unmatched.append(name)
            continue

        used_list_ids.add(list_id)
        current = str(row.get("google_tasks_id", "")).strip()
        if current == list_id:
            continue

        if apply:
            await link_employee_google_tasks_id(
                row, list_id, sheet_row_index=offset + 2
            )
            updated += 1
            logger.info(
                "sync_tasklists_to_employees: linked %r → %r (%s)",
                name,
                list_title,
                list_id,
            )

    my_title = (settings.google_tasks_my_list_title or "Мои задачи").strip()
    my_list = next(
        (
            tl
            for tl in tasklists
            if _normalize_title(str(tl.get("title", ""))) == _normalize_title(my_title)
        ),
        None,
    )
    if my_list and (settings.google_tasks_manager_name or "").strip():
        list_id = str(my_list.get("id", "")).strip()
        for offset, row in enumerate(employees):
            if not is_manager_employee_row(row):
                continue
            name = str(row.get("name", "")).strip()
            current = str(row.get("google_tasks_id", "")).strip()
            if current == list_id:
                used_list_ids.add(list_id)
                break
            if apply:
                await link_employee_google_tasks_id(
                    row, list_id, sheet_row_index=offset + 2
                )
                updated += 1
            used_list_ids.add(list_id)
            if apply:
                logger.info("sync_tasklists_to_employees: manager %r → %s", name, list_id)
            break

    created = 0
    if auto_register_from_lists:
        employees = await list_employees()
        for tl in available:
            list_id = str(tl.get("id", "")).strip()
            list_title = str(tl.get("title", "")).strip()
            if not list_id or not list_title:
                continue
            if list_id in used_list_ids:
                continue
            if _list_id_in_employees(employees, list_id):
                continue
            if _employee_matches_list_title(employees, list_title):
                continue

            if apply:
                await upsert_employee_row(
                    name=list_title,
                    google_tasks_id=list_id,
                )
                logger.info(
                    "sync_tasklists_to_employees: new employees row from list %r",
                    list_title,
                )
            created += 1
            used_list_ids.add(list_id)
            employees.append(
                {
                    "name": list_title,
                    "google_tasks_id": list_id,
                    "active": "true",
                }
            )

    result = {
        "ok": True,
        "updated": updated,
        "created": created,
        "tasklists": len(tasklists),
        "unmatched_employees": unmatched,
    }
    if updated or created:
        logger.info(
            "sync_tasklists_to_employees: done updated=%s created=%s",
            updated,
            created,
        )
    return result
