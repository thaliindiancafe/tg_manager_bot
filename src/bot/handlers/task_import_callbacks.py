"""Callbacks for safe task import drafts."""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.types import CallbackQuery

from src.bot.task_import_safe_mode import (
    cancel_import,
    confirm_import,
    create_unassigned_as_my_tasks,
    format_unassigned_followup,
    skip_unassigned,
    start_await_username,
    _kb_unassigned,
)
from src.bot.reply_format import send_bot_reply
from src.utils.creation_reply import format_created_tasks_lines

logger = logging.getLogger(__name__)

router = Router(name="task_import_callbacks")


@router.callback_query(F.data.startswith("task_import_cancel:"))
async def on_cancel(callback: CallbackQuery) -> None:
    if callback.message is None:
        await callback.answer()
        return
    draft_id = (callback.data or "").split(":", 1)[-1].strip()
    if draft_id:
        await cancel_import(draft_id)
    await callback.answer("Отменено.")
    await callback.message.edit_reply_markup(reply_markup=None)


@router.callback_query(F.data.startswith("task_import_confirm:"))
async def on_confirm(callback: CallbackQuery) -> None:
    if callback.message is None:
        await callback.answer()
        return
    draft_id = (callback.data or "").split(":", 1)[-1].strip()
    await callback.answer()
    if not draft_id:
        await callback.message.answer("Некорректный черновик.")
        return

    result = await confirm_import(draft_id)
    if not result.get("ok"):
        await callback.message.answer(str(result.get("error", "Не удалось создать задачи.")))
        return

    created = result.get("created") or []
    failed = result.get("failed") or []
    text = f"✅ Создано задач с исполнителем: {len(created)}."
    link_lines = format_created_tasks_lines(created)
    if link_lines:
        text += "\n\nПроверить в Google Tasks:\n" + "\n".join(link_lines)
    if failed:
        text += f"\n⚠️ Не создано: {len(failed)}."

    await callback.message.edit_reply_markup(reply_markup=None)
    await send_bot_reply(callback.message, text, raw_html=True)

    if result.get("needs_unassigned_action"):
        unassigned = list(result.get("unassigned") or [])
        follow = format_unassigned_followup(unassigned)
        await send_bot_reply(
            callback.message,
            follow,
            raw_html=True,
            reply_markup=_kb_unassigned(draft_id),
        )


@router.callback_query(F.data.startswith("task_import_my_tasks:"))
async def on_my_tasks(callback: CallbackQuery) -> None:
    if callback.message is None:
        await callback.answer()
        return
    draft_id = (callback.data or "").split(":", 1)[-1].strip()
    await callback.answer()
    result = await create_unassigned_as_my_tasks(draft_id)
    if not result.get("ok"):
        await callback.message.answer(str(result.get("error", "Ошибка")))
        return
    created = result.get("created") or []
    failed = result.get("failed") or []
    assignee = str(result.get("assigned_to", "")).strip()
    text = f"✅ В «мои задачи» ({assignee}): создано {len(created)}."
    link_lines = format_created_tasks_lines(created)
    if link_lines:
        text += "\n\nПроверить в Google Tasks:\n" + "\n".join(link_lines)
    if failed:
        text += f"\n⚠️ Не создано: {len(failed)}."
    await callback.message.edit_reply_markup(reply_markup=None)
    await send_bot_reply(callback.message, text, raw_html=True)


@router.callback_query(F.data.startswith("task_import_assign:"))
async def on_assign_nick(callback: CallbackQuery) -> None:
    if callback.message is None:
        await callback.answer()
        return
    draft_id = (callback.data or "").split(":", 1)[-1].strip()
    await callback.answer()
    result = await start_await_username(draft_id)
    if not result.get("ok"):
        await callback.message.answer(str(result.get("error", "Ошибка")))
        return
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer(
        "Ответьте **одним сообщением** с @username исполнителя для всех оставшихся задач, "
        "например: @asimhayatkhan"
    )


@router.callback_query(F.data.startswith("task_import_skip_unassigned:"))
async def on_skip_unassigned(callback: CallbackQuery) -> None:
    if callback.message is None:
        await callback.answer()
        return
    draft_id = (callback.data or "").split(":", 1)[-1].strip()
    if draft_id:
        await skip_unassigned(draft_id)
    await callback.answer("Пропущено.")
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer("Задачи без исполнителя не созданы.")
