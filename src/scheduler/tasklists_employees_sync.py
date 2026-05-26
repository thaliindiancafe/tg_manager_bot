"""Daily cron: Google Tasks lists → employees.google_tasks_id."""

from __future__ import annotations

import logging

from src.config import settings
from src.google.tasklist_employees_sync import sync_tasklists_to_employees_sheet

logger = logging.getLogger(__name__)


async def sync_tasklists_to_employees_daily() -> dict:
    if not settings.google_tasks_lists_sync_enabled:
        logger.debug("sync_tasklists_to_employees_daily: disabled")
        return {"skipped": True}

    return await sync_tasklists_to_employees_sheet(
        auto_register_from_lists=settings.google_tasks_lists_auto_register,
    )
