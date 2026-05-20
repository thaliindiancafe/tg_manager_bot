"""Resolve Google Tasks list id for an employee row (incl. manager «Мои задачи»)."""

from __future__ import annotations

import logging
import re
import unicodedata
from typing import Any

from src.config import settings
from src.google import sheets
from src.google import tasks as google_tasks
from src.google.oauth_credentials import oauth_configured

logger = logging.getLogger(__name__)

_MY_LIST_CACHE: tuple[str, str] | None = None


def _normalize_title(value: str) -> str:
    text = unicodedata.normalize("NFKC", value or "")
    return text.strip().lower()


def _normalize_manager_key(value: str) -> str:
    return value.strip().lower().lstrip("@")


def is_manager_employee_row(row: dict[str, Any]) -> bool:
    manager_tid = (settings.google_tasks_manager_telegram_id or "").strip()
    if manager_tid:
        row_tid = str(row.get("telegram_user_id", "")).strip()
        if row_tid and row_tid == manager_tid:
            return True

    manager_key = _normalize_manager_key(settings.google_tasks_manager_name or "")
    if not manager_key:
        return False
    name = _normalize_manager_key(str(row.get("name", "")))
    username = _normalize_manager_key(str(row.get("username", "")))
    return name == manager_key or username == manager_key


async def resolve_my_tasks_list() -> tuple[str, str] | None:
    """OAuth list «Мои задачи» / «My Tasks» for the manager account."""
    global _MY_LIST_CACHE
    if _MY_LIST_CACHE is not None:
        return _MY_LIST_CACHE

    want = _normalize_title(settings.google_tasks_my_list_title)
    want_en = "my tasks"
    for tl in await google_tasks.list_tasklists():
        title = str(tl.get("title", "")).strip()
        title_norm = _normalize_title(title)
        if title_norm in {want, want_en}:
            tl_id = str(tl.get("id", "")).strip()
            if tl_id:
                _MY_LIST_CACHE = (tl_id, title)
                return _MY_LIST_CACHE
    return None


def _normalize_name(value: str) -> str:
    text = unicodedata.normalize("NFKC", value or "")
    text = re.sub(r"[^\w\s]", " ", text, flags=re.UNICODE)
    text = re.sub(r"\s+", " ", text).strip().lower()
    return text


def match_tasklist_for_employee_name(
    employee_name: str,
    tasklists: list[dict[str, str]],
) -> dict[str, str] | None:
    """Best matching OAuth task list by employee display name."""
    emp_norm = _normalize_name(employee_name)
    if not emp_norm:
        return None

    best: dict[str, str] | None = None
    best_score = 0

    for tl in tasklists:
        title = str(tl.get("title", "")).strip()
        title_norm = _normalize_name(title)
        if not title_norm:
            continue

        score = 0
        if emp_norm == title_norm:
            score = 100
        elif emp_norm in title_norm or title_norm in emp_norm:
            score = 80
        elif emp_norm.split()[0] == title_norm.split()[0]:
            score = 60

        if score > best_score:
            best_score = score
            best = tl

    return best if best_score >= 60 else None


async def ensure_tasklist_for_employee_row(
    row_index: int,
    row: dict[str, Any],
) -> tuple[str, str] | None:
    """
    Return (tasklist_id, title). Uses google_tasks_id, matches existing list,
    or creates a new list titled with employees.name and updates the sheet.
    """
    existing = await resolve_tasklist_for_employee_row(row)
    if existing:
        return existing

    if not settings.google_tasks_use_oauth or not oauth_configured():
        return None

    name = str(row.get("name", "")).strip()
    if not name:
        return None

    if is_manager_employee_row(row):
        my_list = await resolve_my_tasks_list()
        if my_list:
            return my_list

    try:
        tasklists = await google_tasks.list_tasklists()
        matched = match_tasklist_for_employee_name(name, tasklists)
        if matched:
            tl_id = str(matched.get("id", "")).strip()
            title = str(matched.get("title", "")).strip() or name
        else:
            created = await google_tasks.create_tasklist(name)
            tl_id = str(created.get("id", "")).strip()
            title = str(created.get("title", "")).strip() or name
            logger.info(
                "Created Google Tasks list for employee %r: id=%s",
                name,
                tl_id,
            )

        if not tl_id:
            return None

        updated = dict(row)
        updated["google_tasks_id"] = tl_id
        await sheets.update_row("employees", row_index, updated)
        return tl_id, title
    except Exception as exc:
        logger.error(
            "ensure_tasklist_for_employee_row failed: name=%s error=%s",
            name,
            exc,
            exc_info=True,
        )
        return None


async def resolve_tasklist_for_employee_row(
    row: dict[str, Any],
) -> tuple[str, str] | None:
    """
    Return (tasklist_id, tasklist_title) for this employees row.

    Uses google_tasks_id when set; for the manager row falls back to «Мои задачи».
    """
    tasklist_id = str(row.get("google_tasks_id", "")).strip()
    name = str(row.get("name", "")).strip()
    if tasklist_id:
        return tasklist_id, name or tasklist_id

    if is_manager_employee_row(row):
        my_list = await resolve_my_tasks_list()
        if my_list:
            return my_list
        logger.warning(
            "Manager %r has no google_tasks_id and «%s» list not found in OAuth account",
            name,
            settings.google_tasks_my_list_title,
        )
    return None
