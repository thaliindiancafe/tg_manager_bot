"""Inline keyboard callbacks for choosing an open delegation task."""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.types import CallbackQuery

from src.agent import tools as agent_tools

logger = logging.getLogger(__name__)

router = Router(name="delegation_callbacks")


@router.callback_query(F.data.startswith("delegation_task:"))
async def on_delegation_task_selected(callback: CallbackQuery) -> None:
    if callback.message is None or callback.from_user is None:
        await callback.answer()
        return
    if callback.message.chat.type != ChatType.PRIVATE:
        await callback.answer("Выбор задачи — только в личке с ботом.", show_alert=True)
        return

    task_id = (callback.data or "").split(":", 1)[-1].strip()
    if not task_id:
        await callback.answer("Некорректный ID задачи.", show_alert=True)
        return

    uid = callback.from_user.id
    check = await agent_tools.assert_task_for_telegram_user(task_id, uid)
    if not check.get("ok"):
        await callback.answer(str(check.get("error", "Задача недоступна")), show_alert=True)
        return

    await agent_tools.set_pending_proof_task(uid, task_id)
    await callback.answer()
    title = str(check.get("title", "")).strip() or "поручение"
    await callback.message.answer(
        f"Выбрано: **{title}**\n"
        f"ID: `{task_id}`\n\n"
        "Пришлите **фото** отчёта или напишите, что сделано (можно ответом на сообщение бота с поручением)."
    )
