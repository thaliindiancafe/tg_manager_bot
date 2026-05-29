"""Format user-facing summaries after task/event creation."""

from __future__ import annotations

from typing import Any


def format_created_tasks_lines(created: list[dict[str, Any]], *, max_items: int = 8) -> list[str]:
    """Lines with title + verification_url for batch imports."""
    lines: list[str] = []
    for item in created[:max_items]:
        title = str(item.get("title", "")).strip() or "(без названия)"
        url = str(item.get("verification_url", "")).strip()
        if url:
            lines.append(f"• {title}\n  {url}")
        else:
            lines.append(f"• {title}")
    if len(created) > max_items:
        lines.append(f"…и ещё {len(created) - max_items}.")
    return lines
