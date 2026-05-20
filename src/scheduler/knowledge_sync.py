"""Daily Drive knowledge folder sync (RAG index)."""

from __future__ import annotations

import logging

from src.agent.knowledge import sync_drive_knowledge_folder
from src.config import settings

logger = logging.getLogger(__name__)


async def sync_knowledge_from_drive() -> None:
    """Cron job: re-index DRIVE_KNOWLEDGE_FOLDER_ID if enabled."""
    if not settings.knowledge_sync_enabled:
        logger.debug("sync_knowledge_from_drive skipped: KNOWLEDGE_SYNC_ENABLED=false")
        return

    folder_id = (settings.drive_knowledge_folder_id or "").strip()
    if not folder_id:
        logger.warning(
            "sync_knowledge_from_drive skipped: DRIVE_KNOWLEDGE_FOLDER_ID not set"
        )
        return

    try:
        summary = await sync_drive_knowledge_folder(folder_id)
        if not summary.get("ok"):
            logger.warning("sync_knowledge_from_drive: %s", summary)
    except Exception as exc:
        logger.error("sync_knowledge_from_drive failed: %s", exc, exc_info=True)
