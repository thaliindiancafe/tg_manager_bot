"""Checklist proof evaluation via Gemini (text or photo description)."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

_CHECKLIST_VISION_PROMPT = """Ты проверяешь отчёт сотрудника ресторана по чеклисту поручения.

Пункты чеклиста:
{checklist_lines}

Описание отчёта (текст и/или расшифровка фото):
{proof_text}

Для каждого пункта чеклиста укажи статус:
- ok — пункт явно выполнен / виден на фото
- not_visible — на фото/в тексте нельзя подтвердить этот пункт
- missing — явно не выполнен

Ответь ТОЛЬКО валидным JSON-массивом без markdown, формат:
[
  {{"item": "текст пункта", "status": "ok|not_visible|missing", "comment": "кратко по-русски"}}
]
Порядок пунктов как в чеклисте. Поле item — как в чеклисте."""

_STATUS_ALIASES = {
    "ok": "ok",
    "да": "ok",
    "yes": "ok",
    "not_visible": "not_visible",
    "not visible": "not_visible",
    "не видно": "not_visible",
    "невидно": "not_visible",
    "missing": "missing",
    "нет": "missing",
    "no": "missing",
}


def build_checklist_vision_prompt(checklist: list[str], proof_text: str) -> str:
    lines = "\n".join(f"- {item}" for item in checklist if item.strip())
    return _CHECKLIST_VISION_PROMPT.format(
        checklist_lines=lines or "- (пусто)",
        proof_text=(proof_text or "").strip()[:6000],
    )


def _normalize_item_status(raw: str) -> str:
    key = (raw or "").strip().lower().replace(" ", "_")
    return _STATUS_ALIASES.get(key, "not_visible")


def parse_checklist_evaluation_response(
    text: str,
    checklist: list[str],
) -> list[dict[str, str]]:
    """Parse model JSON; fallback maps checklist order to not_visible."""
    raw = (text or "").strip()
    if not raw:
        return _fallback_items(checklist, "not_visible")

    blob = raw
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", raw)
    if fence:
        blob = fence.group(1).strip()
    else:
        start = raw.find("[")
        end = raw.rfind("]")
        if start >= 0 and end > start:
            blob = raw[start : end + 1]

    try:
        data = json.loads(blob)
    except json.JSONDecodeError:
        logger.warning("checklist proof JSON parse failed, using fallback")
        return _fallback_items(checklist, "not_visible")

    if not isinstance(data, list):
        return _fallback_items(checklist, "not_visible")

    out: list[dict[str, str]] = []
    for idx, entry in enumerate(data):
        if not isinstance(entry, dict):
            continue
        item = str(entry.get("item", "")).strip()
        if not item and idx < len(checklist):
            item = checklist[idx]
        status = _normalize_item_status(str(entry.get("status", "")))
        comment = str(entry.get("comment", "")).strip()
        out.append({"item": item, "status": status, "comment": comment})

    if len(out) < len(checklist):
        known = {row["item"] for row in out}
        for item in checklist:
            if item not in known:
                out.append({"item": item, "status": "not_visible", "comment": ""})

    return out or _fallback_items(checklist, "not_visible")


def _fallback_items(checklist: list[str], status: str) -> list[dict[str, str]]:
    return [{"item": item, "status": status, "comment": ""} for item in checklist if item]


def all_items_ok(items: list[dict[str, str]]) -> bool:
    return bool(items) and all(row.get("status") == "ok" for row in items)


def format_proof_summary_ru(items: list[dict[str, str]]) -> str:
    labels = {"ok": "✓", "not_visible": "?", "missing": "✗"}
    lines: list[str] = []
    for row in items:
        mark = labels.get(str(row.get("status", "")), "?")
        line = f"{mark} {row.get('item', '')}"
        comment = str(row.get("comment", "")).strip()
        if comment:
            line += f" — {comment}"
        lines.append(line)
    return "\n".join(lines)
