"""Pre-agent routing for private delegation reports (phase 5)."""

from __future__ import annotations

import logging

from aiogram import Bot
from aiogram.enums import ChatType
from aiogram.types import Message

from src.agent import tools as agent_tools
from src.bot.delegation_proof import (
    build_task_choice_keyboard,
    is_completion_like_message,
)
from src.bot.delegation_reply import (
    extract_reply_delegation_task_id,
    extract_task_id_from_user_text,
)

logger = logging.getLogger(__name__)


async def resolve_proof_task_id(message: Message) -> str | None:
    if message.from_user is None:
        return None
    uid = message.from_user.id
    reply_tid = extract_reply_delegation_task_id(message)
    if reply_tid:
        return reply_tid
    text_tid = extract_task_id_from_user_text(message.text or message.caption or "")
    if text_tid:
        return text_tid
    pending = await agent_tools.pop_pending_proof_task(uid)
    if pending:
        return pending
    return await agent_tools.resolve_single_open_task_id(uid)


async def maybe_offer_task_choice(message: Message) -> bool:
    """
    If private chat, 2+ open tasks, no task binding — send inline keyboard.
    Returns True when handled (agent not needed).
    """
    if message.chat.type != ChatType.PRIVATE or message.from_user is None:
        return False
    if extract_reply_delegation_task_id(message):
        return False
    if extract_task_id_from_user_text(message.text or ""):
        return False

    text = (message.text or "").strip()
    if not is_completion_like_message(text):
        return False

    uid = message.from_user.id
    data = await agent_tools.get_open_tasks_for_telegram_user(uid)
    open_tasks = data.get("open_tasks") or []
    if len(open_tasks) < 2:
        return False

    keyboard = build_task_choice_keyboard(open_tasks)
    lines = [
        "У вас несколько открытых поручений. Выберите задачу кнопкой или ответьте **реплаем** на нужное сообщение бота:",
    ]
    for t in open_tasks[:6]:
        lines.append(
            f"• `{t.get('task_id', '')}` — {t.get('title', '')} ({t.get('status', '')})"
        )
    await message.answer("\n".join(lines), reply_markup=keyboard)
    return True


async def try_handle_photo_proof(
    message: Message,
    image_base64: str,
    mime_type: str,
) -> bool:
    """Process photo as checklist proof when task_id is known. Returns True if handled."""
    if message.chat.type != ChatType.PRIVATE or message.from_user is None:
        return False

    task_id = await resolve_proof_task_id(message)
    if not task_id:
        return False

    uid = message.from_user.id
    caption = (message.caption or "").strip()
    try:
        result = await agent_tools.submit_task_proof_from_image(
            task_id,
            uid,
            image_base64,
            mime_type=mime_type,
            caption=caption,
        )
        if not result.get("ok", True) and result.get("error"):
            await message.answer(str(result["error"]))
            return True
        await message.answer(agent_tools.format_proof_result_message(result))
        return True
    except Exception as exc:
        logger.error(
            "try_handle_photo_proof failed: task_id=%s uid=%s %s",
            task_id,
            uid,
            exc,
            exc_info=True,
        )
        return False
