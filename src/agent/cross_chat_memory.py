"""Format recent dialogue from other Telegram chats for the agent system prompt."""

from __future__ import annotations

import re
from typing import Any

_CONTEXT_PREFIX_RE = re.compile(
    r"^\[Контекст:.*?\]\s*\n+",
    re.DOTALL,
)


def strip_internal_context_prefix(content: str) -> str:
    """Remove private-chat tool routing prefix before showing in cross-chat summary."""
    text = (content or "").strip()
    if not text.startswith("[Контекст:"):
        return text
    return _CONTEXT_PREFIX_RE.sub("", text, count=1).strip() or text


def _role_label(role: str) -> str:
    value = (role or "user").strip().lower()
    if value in {"assistant", "bot", "model"}:
        return "ассистент"
    return "пользователь"


def build_cross_chat_system_section(
    rows: list[dict[str, Any]],
    chat_labels: dict[str, str],
    *,
    max_content_chars: int,
) -> str:
    """
    Build markdown block for system instruction (chronological, oldest first).

    rows: memory_history entries from other chats, newest-first (as from sheets).
    """
    if not rows:
        return ""

    ordered = list(reversed(rows))
    lines: list[str] = []
    cap = max(80, int(max_content_chars))

    for row in ordered:
        chat_id = str(row.get("chat_id", "")).strip()
        label = chat_labels.get(chat_id) or f"chat {chat_id}"
        ts = str(row.get("timestamp", "")).strip()
        role = _role_label(str(row.get("role", "user")))
        body = strip_internal_context_prefix(str(row.get("content", "")))
        if len(body) > cap:
            body = body[: cap - 3] + "..."
        if not body:
            continue
        ts_part = f" | {ts}" if ts else ""
        lines.append(f"- [{label}{ts_part}] {role}: {body}")

    if not lines:
        return ""

    header = (
        "## Недавно в других чатах (общая память)\n"
        "Ниже — фрагменты переписки из **других** чатов с ботом (не текущий диалог). "
        "Используй для контекста («что обсуждали в группе», договорённости). "
        "Не путай с сообщением пользователя в этом чате.\n"
    )
    return header + "\n".join(lines)
