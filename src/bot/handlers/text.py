"""Handler for plain text messages."""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.types import Message

from src.agent.client import ERROR_MESSAGE, call_agent
from src.bot.typing_indicator import typing_while
from src.bot.handlers.status import is_status_message
from src.bot.delegation_reply import extract_reply_delegation_task_id
from src.bot.delegation_reply import extract_task_id_from_user_text
from src.bot.calendar_events_query import try_reply_calendar_events_query
from src.bot.employee_register_query import try_reply_employee_register_bulk
from src.bot.employee_tasks_query import try_reply_employee_tasks_query
from src.bot.schedule_query import try_reply_schedule_query
from src.bot.tasks_for_dates_query import try_reply_tasks_for_dates_query
from src.bot.handlers.delegation_routing import maybe_offer_task_choice
from src.bot.group_gate import should_process_group_message
from src.bot.private_agent_message import with_private_telegram_context
from src.bot.reply_format import send_bot_reply
from src.google import sheets

logger = logging.getLogger(__name__)

router = Router(name="text")


@router.message(
    F.text,
    ~F.text.startswith("/"),
    ~F.func(is_status_message),
    F.chat.type.in_({ChatType.PRIVATE, ChatType.GROUP, ChatType.SUPERGROUP}),
)
async def handle_text_message(message: Message) -> None:
    """Route user text to the Gemini agent and reply in the same chat."""
    try:
        text = (message.text or "").strip()
        if not text:
            return

        if not await should_process_group_message(message):
            return

        chat_id = int(message.chat.id)
        is_group = message.chat.type in {ChatType.GROUP, ChatType.SUPERGROUP}
        thread_id = message.message_thread_id

        async with typing_while(message.bot, chat_id, message_thread_id=thread_id):
            if await maybe_offer_task_choice(message):
                return

            register_reply = await try_reply_employee_register_bulk(text)
            if register_reply is not None:
                await send_bot_reply(message, register_reply, raw_html=True)
                return

            uid = message.from_user.id if message.from_user else None
            fast_reply = await try_reply_employee_tasks_query(
                text,
                telegram_user_id=uid,
            )
            if fast_reply is None:
                fast_reply = await try_reply_tasks_for_dates_query(text)
            if fast_reply is None:
                fast_reply = await try_reply_schedule_query(text)
            if fast_reply is None:
                fast_reply = await try_reply_calendar_events_query(text)
            if fast_reply is not None:
                reply = fast_reply
            else:
                history = await sheets.get_recent_history(chat_id, limit=30)
                routed = text
                if message.chat.type == ChatType.PRIVATE:
                    uid = message.from_user.id if message.from_user else chat_id
                    reply_tid = extract_reply_delegation_task_id(message)
                    if not reply_tid:
                        reply_tid = extract_task_id_from_user_text(text)
                    routed = with_private_telegram_context(
                        uid, text, reply_task_id=reply_tid
                    )
                reply = await call_agent(
                    routed,
                    chat_id,
                    history,
                    group_chat_mode=is_group,
                )

        await send_bot_reply(message, reply, raw_html=True)
    except Exception as exc:
        logger.error(
            "handle_text_message failed: chat_id=%s error=%s",
            getattr(message.chat, "id", None),
            exc,
            exc_info=True,
        )
        await send_bot_reply(message, ERROR_MESSAGE)
