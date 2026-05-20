"""Handler for incoming photos."""

from __future__ import annotations

import base64
import logging
from io import BytesIO

from aiogram import Bot, F, Router
from aiogram.enums import ChatType
from aiogram.types import Message

from src.agent.client import ERROR_MESSAGE, call_agent, describe_photo
from src.bot.typing_indicator import typing_while
from src.bot.delegation_reply import extract_reply_delegation_task_id
from src.bot.handlers.delegation_routing import try_handle_photo_proof
from src.bot.group_gate import should_process_group_message
from src.bot.private_agent_message import with_private_telegram_context
from src.google import sheets

logger = logging.getLogger(__name__)

router = Router(name="photo")


def _photo_mime_type(message: Message) -> str:
    if message.document and message.document.mime_type:
        return message.document.mime_type
    return "image/jpeg"


@router.message(
    F.photo,
    F.chat.type.in_({ChatType.PRIVATE, ChatType.GROUP, ChatType.SUPERGROUP}),
)
async def handle_photo_message(message: Message, bot: Bot) -> None:
    """Download photo, describe via Gemini Vision, then route to the agent."""
    try:
        if not message.photo:
            return

        photo = message.photo[-1]
        file = await bot.get_file(photo.file_id)

        buffer = BytesIO()
        await bot.download(file, destination=buffer)
        image_base64 = base64.b64encode(buffer.getvalue()).decode("utf-8")
        mime = _photo_mime_type(message)

        if await try_handle_photo_proof(message, image_base64, mime):
            return

        if not await should_process_group_message(message):
            return

        chat_id = int(message.chat.id)
        is_group = message.chat.type in {ChatType.GROUP, ChatType.SUPERGROUP}
        thread_id = message.message_thread_id

        async with typing_while(bot, chat_id, message_thread_id=thread_id):
            description = await describe_photo(image_base64, mime_type=mime)

            caption = (message.caption or "").strip()
            if caption:
                agent_message = (
                    f"[Пользователь отправил фото]\n"
                    f"Подпись: {caption}\n\n"
                    f"Описание фото:\n{description}"
                )
            else:
                agent_message = (
                    f"[Пользователь отправил фото]\n\nОписание фото:\n{description}"
                )

            history = await sheets.get_recent_history(chat_id, limit=30)
            routed = agent_message
            if message.chat.type == ChatType.PRIVATE:
                uid = message.from_user.id if message.from_user else chat_id
                reply_tid = extract_reply_delegation_task_id(message)
                routed = with_private_telegram_context(
                    uid, agent_message, reply_task_id=reply_tid
                )
            reply = await call_agent(
                routed,
                chat_id,
                history,
                group_chat_mode=is_group,
            )

        await message.answer(reply)
    except Exception as exc:
        logger.error(
            "handle_photo_message failed: chat_id=%s error=%s",
            getattr(message.chat, "id", None),
            exc,
            exc_info=True,
        )
        await message.answer(ERROR_MESSAGE)
