"""Handler for /chatid — expose Telegram chat_id for chats sheet setup."""

from __future__ import annotations

import logging

from aiogram import Router
from aiogram.enums import ChatType
from aiogram.filters import Command
from aiogram.types import Message

logger = logging.getLogger(__name__)

router = Router(name="chatid")


@router.message(Command("chatid"))
async def cmd_chatid(message: Message) -> None:
    """Return chat_id and title for bot chat registry."""
    try:
        chat = message.chat
        chat_id = int(chat.id)
        title = (chat.title or chat.full_name or chat.username or "без названия").strip()
        chat_type = chat.type.value if hasattr(chat.type, "value") else str(chat.type)

        lines = [
            f"chat_id: `{chat_id}`",
            f"Название: {title}",
            f"Тип: {chat_type}",
            "",
            "Передайте chat_id администратору бота для подключения этого чата.",
            "Нужны: chat_id, название чата, active=true.",
        ]
        if chat.type == ChatType.PRIVATE:
            lines.insert(
                0,
                "Это личный чат. Для рабочей группы вызовите /chatid в группе, куда добавлен бот.",
            )

        await message.answer("\n".join(lines))
    except Exception as exc:
        logger.error("cmd_chatid failed: %s", exc, exc_info=True)
        await message.answer("Не удалось получить chat_id. Попробуйте позже.")
