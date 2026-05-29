"""Fast path: bulk register employees into Sheets without LLM."""

from __future__ import annotations

import logging
import re
import ssl
from html import escape

from src.agent import tools as agent_tools
from src.utils.employee_register_parse import parse_employees_bulk_text
from src.utils.telegram_reply import format_bot_reply, format_note

logger = logging.getLogger(__name__)

_REGISTER_INTENT = re.compile(
    r"внеси|добав(ь|ить)|зарегистрир|запиши|обнови",
    re.IGNORECASE,
)
_EMPLOYEE_CONTEXT = re.compile(
    r"сотрудник|employees|базу\s+данн|справочник|команд",
    re.IGNORECASE,
)
# «добавь задачи», «проставь в гугл таск», «исследуй диалог» — не регистрация сотрудников
_NOT_EMPLOYEE_REGISTER = re.compile(
    r"задач|поручен|гугл\s*таск|google\s*task|календар|мероприят|"
    r"диалог|истори|исследуй|проставь|последн",
    re.IGNORECASE,
)


def _looks_like_bulk_register(text: str) -> bool:
    if not _REGISTER_INTENT.search(text):
        return False
    if _NOT_EMPLOYEE_REGISTER.search(text):
        return False
    parsed = parse_employees_bulk_text(text)
    if len(parsed) >= 2:
        return True
    # Одна строка — только явный контекст employees (не любой @ в тексте)
    if len(parsed) == 1 and _EMPLOYEE_CONTEXT.search(text):
        return True
    return False


def _format_bulk_reply(data: dict) -> str:
    if not data.get("ok_count"):
        err = str(data.get("error", "")).strip()
        return format_bot_reply(err or "Не удалось добавить сотрудников. Проверьте формат строк.")

    lines = [
        f"✅ В справочник сотрудников добавлено: {data['ok_count']} из "
        f"{data.get('total_parsed', data['ok_count'])}.",
    ]
    failed = data.get("failed") or []
    if failed:
        lines.append("")
        lines.append("⚠️ Не записаны:")
        for row in failed:
            lines.append(
                f"- {escape(str(row.get('name', '?')))}: "
                f"{escape(str(row.get('error', 'ошибка')))}"
            )

    lines.append("")
    lines.append(
        format_note(
            "Когда сотрудники напишут боту /start в личке, "
            "подставится Telegram ID по @username."
        )
    )
    return "\n\n".join(lines)


def _register_error_message(exc: BaseException) -> str:
    if isinstance(exc, (ssl.SSLError, TimeoutError, ConnectionError, OSError)):
        return format_bot_reply(
            "Не удалось сохранить сотрудников из-за сбоя сети. "
            "Подождите минуту и отправьте список ещё раз."
        )
    return format_bot_reply(
        "Не удалось добавить сотрудников. Попробуйте ещё раз или по одному человеку."
    )


async def try_reply_employee_register_bulk(text: str) -> str | None:
    if not _looks_like_bulk_register(text):
        return None

    logger.info("employee_register fast path: bulk register")
    try:
        data = await agent_tools.register_employees_bulk(text)
        return _format_bulk_reply(data)
    except Exception as exc:
        logger.error("employee_register bulk failed: %s", exc, exc_info=True)
        return _register_error_message(exc)
