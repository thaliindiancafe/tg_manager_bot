"""When to invoke the agent in group/supergroup chats (anti-flood)."""

from __future__ import annotations

import logging
import re

from aiogram.enums import ChatType
from aiogram.types import Message

from src.config import settings

logger = logging.getLogger(__name__)

_MENTION_RE = re.compile(r"@([a-zA-Z][a-zA-Z0-9_]{4,})")


async def should_process_group_message(message: Message) -> bool:
    """
    In groups, process only when the bot is explicitly addressed.

    True when: reply to this bot, @mention, text_mention of bot, or @username in text.
    Private chats always return True.
    """
    if message.chat.type not in {ChatType.GROUP, ChatType.SUPERGROUP}:
        return True

    if not settings.group_agent_require_mention:
        return True

    bot = message.bot
    if bot is None:
        return False

    me = await bot.me()
    bot_id = me.id
    bot_username = (me.username or "").strip().lower()

    reply = message.reply_to_message
    if reply and reply.from_user and reply.from_user.id == bot_id:
        return True

    text = (message.text or message.caption or "").strip()
    entities = message.entities or message.caption_entities or []

    for ent in entities:
        if ent.type == "text_mention" and ent.user and ent.user.id == bot_id:
            return True
        if ent.type == "mention" and bot_username and text:
            fragment = text[ent.offset : ent.offset + ent.length].lstrip("@").lower()
            if fragment == bot_username:
                return True

    if bot_username:
        if f"@{bot_username}" in text.lower():
            return True
        for match in _MENTION_RE.finditer(text):
            if match.group(1).lower() == bot_username:
                return True

    logger.debug(
        "group_gate skip: chat_id=%s user=%s",
        message.chat.id,
        message.from_user.id if message.from_user else None,
    )
    return False
