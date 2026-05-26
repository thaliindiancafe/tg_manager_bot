"""Handler for task status phrases in text messages."""

from __future__ import annotations

import logging
import re

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.types import Message

from src.agent.client import ERROR_MESSAGE, call_agent
from src.bot.reply_format import send_bot_reply
from src.bot.group_gate import should_process_group_message
from src.bot.typing_indicator import typing_while
from src.google import sheets

logger = logging.getLogger(__name__)

router = Router(name="status")

STATUS_PATTERN = re.compile(
    r"не сделано|не успел|выполнено|перенести|сделано|готово",
    re.IGNORECASE,
)


def is_status_message(message: Message) -> bool:
    """Return True if message text contains a task status keyword."""
    text = message.text or ""
    return STATUS_PATTERN.search(text) is not None


@router.message(
    F.text,
    ~F.text.startswith("/"),
    F.func(is_status_message),
    F.chat.type.in_({ChatType.PRIVATE, ChatType.GROUP, ChatType.SUPERGROUP}),
)
async def handle_status_message(message: Message) -> None:
    """Forward status updates to the agent for tool-based task actions."""
    try:
        text = (message.text or "").strip()
        if not text:
            return

        if not await should_process_group_message(message):
            return

        chat_id = int(message.chat.id)
        is_group = message.chat.type in {ChatType.GROUP, ChatType.SUPERGROUP}
        async with typing_while(
            message.bot,
            chat_id,
            message_thread_id=message.message_thread_id,
        ):
            history = await sheets.get_recent_history(chat_id, limit=30)
            reply = await call_agent(
                text,
                chat_id,
                history,
                group_chat_mode=is_group,
            )
        await send_bot_reply(message, reply, raw_html=True)
    except Exception as exc:
        logger.error(
            "handle_status_message failed: chat_id=%s error=%s",
            getattr(message.chat, "id", None),
            exc,
            exc_info=True,
        )
        await send_bot_reply(message, ERROR_MESSAGE)
