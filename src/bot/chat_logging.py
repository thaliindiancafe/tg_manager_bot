"""Chat transcript logging for Mira-like features.

When STORAGE_BACKEND=db, we persist incoming group messages into DB so the agent
can later extract tasks from last N days even if the bot was not mentioned.
"""

from __future__ import annotations

import logging
from datetime import datetime

from aiogram.enums import ChatType
from aiogram.types import Message

from src.config import settings
from src.storage import get_store
from src.storage.base import ChatMessageRow

logger = logging.getLogger(__name__)


def _is_enabled() -> bool:
    return (getattr(settings, "storage_backend", "sheets") or "sheets").strip().lower() == "db"


async def log_incoming_message(message: Message) -> None:
    """Persist message text/caption into DB; no-op when disabled."""
    try:
        if not _is_enabled():
            return
        if message.chat.type not in {ChatType.GROUP, ChatType.SUPERGROUP}:
            return
        text = (message.text or message.caption or "").strip()
        if not text:
            return

        u = message.from_user
        username = (u.username or "").strip() if u else ""
        full_name = ""
        if u:
            full_name = " ".join([p for p in [u.first_name, u.last_name] if p]).strip()

        created_at = message.date if isinstance(message.date, datetime) else datetime.utcnow()

        await get_store().chat_messages.append_chat_message(
            ChatMessageRow(
                chat_id=int(message.chat.id),
                message_id=int(message.message_id),
                user_id=int(u.id) if u else None,
                username=username,
                full_name=full_name,
                text=text,
                created_at=created_at,
            )
        )
    except Exception as exc:
        logger.warning("chat log skipped: %s", exc)

