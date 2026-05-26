"""Send formatted bot replies (Telegram HTML)."""

from __future__ import annotations

from aiogram.enums import ParseMode
from aiogram.types import Message

from src.utils.telegram_reply import format_bot_reply


async def send_bot_reply(
    message: Message,
    text: str,
    *,
    raw_html: bool = False,
    reply_markup=None,
) -> None:
    body = text if raw_html else format_bot_reply(text)
    await message.answer(
        body,
        parse_mode=ParseMode.HTML,
        reply_markup=reply_markup,
    )
