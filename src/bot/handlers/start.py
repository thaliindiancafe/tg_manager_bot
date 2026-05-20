"""Handler for /start command."""

from __future__ import annotations

import logging

from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.types import Message

from src.agent.tools import (
    _employee_row_by_telegram_id,
    _employee_rows_by_username,
)
from src.google import sheets

logger = logging.getLogger(__name__)

router = Router(name="start")

WELCOME_NEW = (
    "Привет! Я ассистент команды Тхали и Карри.\n"
    "Помогаю с задачами, расписанием и мероприятиями.\n\n"
    "Поручения и напоминания приходят **сюда, в личные сообщения** с этим ботом.\n"
    "На поручение лучше отвечать **реплаем** на моё сообщение (фото или текст).\n\n"
    "Напиши, чем могу помочь."
)
WELCOME_BACK = (
    "Рад снова тебя видеть!\n\n"
    "Напоминания и личные сообщения от меня будут приходить сюда, в этот чат."
)
WELCOME_LINKED = (
    "Привет, {name}! Твой Telegram привязан к профилю в списке сотрудников.\n\n"
    "Напоминания и поручения от бота будут приходить сюда в личные сообщения."
)


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    """Link Telegram user to employees row (by id or by @username) and welcome."""
    try:
        if message.from_user is None:
            await message.answer("Не удалось определить пользователя Telegram.")
            return

        telegram_user_id = int(message.from_user.id)
        tid_str = str(telegram_user_id)
        username = (message.from_user.username or "").strip()
        display_name = username or "Сотрудник"

        row_idx, employee = await _employee_row_by_telegram_id(tid_str)
        if employee is not None:
            await message.answer(WELCOME_BACK)
            return

        if username:
            matches = await _employee_rows_by_username(username)
            without_id = [
                (idx, row)
                for idx, row in matches
                if not str(row.get("telegram_user_id", "")).strip()
            ]
            if len(without_id) == 1:
                idx, row = without_id[0]
                merged = dict(row)
                merged["telegram_user_id"] = tid_str
                merged["username"] = username
                await sheets.update_row("employees", idx, merged)
                human_name = str(row.get("name", "")).strip() or display_name
                await message.answer(WELCOME_LINKED.format(name=human_name))
                return
            if len(without_id) > 1:
                await message.answer(
                    "Нашла несколько строк в списке сотрудников с твоим @username. "
                    "Попроси руководителя поправить таблицу или обратись к администратору бота."
                )
                return
            if matches:
                await message.answer(
                    "Твой @username уже указан у другого сотрудника с привязанным Telegram. "
                    "Обратись к руководителю, чтобы проверили лист employees."
                )
                return

        await sheets.append_row(
            "employees",
            {
                "name": display_name,
                "telegram_user_id": tid_str,
                "username": username,
                "role": "",
                "google_tasks_id": "",
                "active": "true",
            },
        )
        await message.answer(WELCOME_NEW)
    except Exception as exc:
        logger.error(
            "cmd_start failed: user_id=%s error=%s",
            getattr(message.from_user, "id", None),
            exc,
            exc_info=True,
        )
        await message.answer("Произошла ошибка при регистрации. Попробуйте позже.")
