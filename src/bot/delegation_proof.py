"""Private-chat helpers: task choice keyboard and proof routing (phase 5)."""

from __future__ import annotations

import re

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

_COMPLETION_HINT_RE = re.compile(
    r"\b(готово|сделал[аио]?|выполнил[аио]?|отч[её]т|сделано|done)\b",
    re.IGNORECASE,
)


def is_completion_like_message(text: str) -> bool:
    return bool(_COMPLETION_HINT_RE.search((text or "").strip()))


def build_task_choice_keyboard(open_tasks: list[dict]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for task in open_tasks[:8]:
        task_id = str(task.get("task_id", "")).strip()
        if not task_id:
            continue
        title = str(task.get("title", "Задача")).strip()[:36]
        rows.append(
            [
                InlineKeyboardButton(
                    text=title or task_id[:8],
                    callback_data=f"delegation_task:{task_id}",
                )
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows or [])
