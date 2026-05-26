"""Format bot replies for Telegram (HTML parse_mode)."""

from __future__ import annotations

import html
import re

_DASHES = str.maketrans(
    {
        "\u2014": "-",  # em dash
        "\u2013": "-",  # en dash
        "\u2212": "-",  # minus
    }
)

_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")


def normalize_dashes(text: str) -> str:
    return (text or "").translate(_DASHES)


def format_note(text: str) -> str:
    """Hint / note line: ℹ + italic."""
    clean = normalize_dashes((text or "").strip())
    if not clean:
        return ""
    if clean.startswith("ℹ"):
        clean = clean[1:].strip()
    return f"ℹ <i>{html.escape(clean)}</i>"


def _strip_markdown(text: str) -> str:
    text = _BOLD_RE.sub(r"\1", text)
    text = _LINK_RE.sub(r"\1", text)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    return text


def _is_hint_block(block: str) -> bool:
    stripped = block.strip()
    if not stripped:
        return False
    lower = stripped.lower()
    return (
        stripped.startswith("ℹ")
        or lower.startswith("если нужно")
        or lower.startswith("примечание")
        or lower.startswith("подсказка")
        or lower.startswith("важно:")
    )


def format_bot_reply(text: str) -> str:
    """
    Normalize LLM / plain replies: short dashes, no Markdown bold, HTML-safe.
    Hint blocks become ℹ + italic.
    """
    raw = normalize_dashes((text or "").strip())
    if not raw:
        return ""

    raw = _strip_markdown(raw)
    blocks = re.split(r"\n\s*\n", raw)
    out: list[str] = []

    for block in blocks:
        stripped = block.strip()
        if not stripped:
            continue
        if _is_hint_block(stripped):
            out.append(format_note(stripped))
        else:
            out.append(html.escape(stripped))

    return "\n\n".join(out)
