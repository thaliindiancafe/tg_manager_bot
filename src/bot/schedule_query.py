"""Direct answers for shift schedule queries without Gemini."""

from __future__ import annotations

import logging
import re
from typing import Any

from src.agent import tools as agent_tools
from src.bot.employee_tasks_query import _detect_date_preset

logger = logging.getLogger(__name__)

_SCHEDULE_WORD = re.compile(
    r"смен|график|на\s+смене|работает|кто\s+(сегодня|завтра|вчера)",
    re.IGNORECASE,
)


def _looks_like_schedule_query(text: str) -> bool:
    if not _SCHEDULE_WORD.search(text):
        return False
    preset, _ = _detect_date_preset(text)
    return bool(preset)


def _format_schedule_reply(rows: list[dict[str, Any]], dates: list[str]) -> str:
    date_label = ", ".join(dates) if dates else "указанный день"
    if not rows:
        return f"На {date_label} в графике смен никого нет."

    lines = [f"Смены на {date_label}:"]
    for row in rows:
        employee = str(row.get("employee", "")).strip()
        role = str(row.get("role", "")).strip()
        start = str(row.get("shift_start", "")).strip()
        end = str(row.get("shift_end", "")).strip()
        time_part = f" {start}–{end}" if start or end else ""
        role_part = f" ({role})" if role else ""
        lines.append(f"• {employee}{role_part}{time_part}")
    return "\n".join(lines)


async def try_reply_schedule_query(text: str) -> str | None:
    if not _looks_like_schedule_query(text):
        return None

    preset, dates = _detect_date_preset(text)
    if not preset:
        return None

    logger.info("schedule fast path: preset=%s dates=%s", preset, dates)
    target_dates = agent_tools.schedule_target_dates(preset=preset, dates=dates)
    rows = await agent_tools.get_schedule_for_dates(preset=preset, dates=dates)
    return _format_schedule_reply(rows, target_dates)
