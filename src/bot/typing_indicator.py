"""Telegram chat action (typing) while long operations run."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from aiogram import Bot
from aiogram.utils.chat_action import ChatActionSender


@asynccontextmanager
async def typing_while(
    bot: Bot,
    chat_id: int,
    *,
    message_thread_id: int | None = None,
) -> AsyncIterator[None]:
    """Show «печатает…» in the chat until the wrapped block finishes."""
    async with ChatActionSender.typing(
        bot=bot,
        chat_id=chat_id,
        message_thread_id=message_thread_id,
    ):
        yield
