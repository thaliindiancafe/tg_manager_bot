"""Resolve task_id from a reply to the bot's delegation message (private chat, phase 3)."""

from __future__ import annotations

import re

from aiogram.types import Message

_TASK_UUID_RE = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)


def _text_from_message(msg: Message) -> str:
    parts: list[str] = []
    if msg.text:
        parts.append(msg.text)
    if msg.caption:
        parts.append(msg.caption)
    return "\n".join(parts)


def extract_task_id_from_bot_message(msg: Message | None) -> str | None:
    """First UUID in text/caption if the message is from a bot (delegation footer contains task_id)."""
    if msg is None:
        return None
    if msg.from_user is None or not msg.from_user.is_bot:
        return None
    blob = _text_from_message(msg).strip()
    if not blob:
        return None
    m = _TASK_UUID_RE.search(blob)
    return m.group(0) if m else None


def extract_reply_delegation_task_id(message: Message) -> str | None:
    """task_id from the message user replied to, if that message is from a bot."""
    return extract_task_id_from_bot_message(message.reply_to_message)


def extract_task_id_from_user_text(text: str) -> str | None:
    """First UUID in user message (phase 5.6 fallback without reply)."""
    blob = (text or "").strip()
    if not blob:
        return None
    m = _TASK_UUID_RE.search(blob)
    return m.group(0) if m else None
