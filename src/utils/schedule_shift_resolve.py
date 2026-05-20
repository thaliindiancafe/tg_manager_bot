"""Resolve «помощник на смене» from today's schedule (goods unpacking block)."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from src.config import settings
from src.google import sheets
from src.utils.employee_name_match import match_employee_name
from src.utils.employee_role_resolve import EmployeeResolveResult

logger = logging.getLogger(__name__)

# Phrases → schedule lookup (not employees.role)
_SHIFT_UNPACKING_QUERY_TOKENS: frozenset[str] = frozenset(
    {
        "помощникнасмене",
        "помощникпосмене",
        "помощниксмены",
        "помощникнаразборе",
        "разбортовара",
        "кторазбираеттовар",
        "ктонаразборе",
        "unpacking",
        "goodsunpacking",
        "shiftunpacking",
    }
)

_DEFAULT_SCHEDULE_UNPACKING_ROLES: tuple[str, ...] = ("Kleener", "kleener")


def _normalize_token(text: str) -> str:
    t = (text or "").strip().lower()
    t = re.sub(r"[@«»\"'.,!?]", "", t)
    t = re.sub(r"[\s\-_]+", "", t)
    return t


def is_shift_unpacking_query(query: str) -> bool:
    """True if query means «who is on goods unpacking shift today» (from schedule)."""
    q = (query or "").strip()
    if not q:
        return False
    token = _normalize_token(q)
    if token in _SHIFT_UNPACKING_QUERY_TOKENS:
        return True
    low = q.lower()
    if "разбор" in low and "товар" in low:
        return True
    if "помощник" in low and re.search(r"смен", low):
        return True
    if re.search(r"кто\s+.*разбор", low):
        return True
    return False


def _schedule_unpacking_role_labels() -> set[str]:
    """Role values in schedule.role that mean «разбор товара» (case-insensitive match)."""
    raw = (getattr(settings, "schedule_unpacking_roles", "") or "").strip()
    labels: list[str] = list(_DEFAULT_SCHEDULE_UNPACKING_ROLES)
    if raw:
        if raw.startswith("["):
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, list):
                    labels = [str(x).strip() for x in parsed if str(x).strip()]
            except json.JSONDecodeError:
                pass
        else:
            labels = [p.strip() for p in raw.split(",") if p.strip()]
    return {label.lower() for label in labels if label}


def _today_iso() -> str:
    tz = ZoneInfo(settings.timezone)
    return datetime.now(tz).strftime("%Y-%m-%d")


def _normalize_date_cell(value: Any) -> str:
    return str(value or "").strip()[:10]


def _has_shift_times(row: dict[str, Any]) -> bool:
    start = str(row.get("shift_start", "")).strip()
    end = str(row.get("shift_end", "")).strip()
    return bool(start or end)


async def resolve_shift_unpacking_from_schedule(
    employee_rows: list[dict[str, Any]],
    *,
    target_date: str | None = None,
) -> EmployeeResolveResult:
    """
    Who is on unpacking shift on target_date (default today) per schedule.role.

    Matches schedule.employee to employees.name; returns canonical name from employees.
    """
    day = (target_date or _today_iso()).strip()[:10]
    role_labels = _schedule_unpacking_role_labels()
    if not role_labels:
        return EmployeeResolveResult(
            ok=False,
            error="Не настроены роли графика для разбора товара (SCHEDULE_UNPACKING_ROLES).",
        )

    try:
        schedule = await sheets.read_sheet("schedule")
    except Exception as exc:
        logger.error("resolve_shift_unpacking: schedule read failed: %s", exc)
        return EmployeeResolveResult(
            ok=False,
            error="Не удалось прочитать лист schedule.",
        )

    on_shift: list[tuple[str, str]] = []  # (employee from schedule, schedule role)
    for row in schedule:
        if _normalize_date_cell(row.get("date")) != day:
            continue
        sched_role = str(row.get("role", "")).strip()
        if sched_role.lower() not in role_labels:
            continue
        if not _has_shift_times(row):
            continue
        emp = str(row.get("employee", "")).strip()
        if emp:
            on_shift.append((emp, sched_role))

    if not on_shift:
        roles_hint = ", ".join(sorted(role_labels))
        return EmployeeResolveResult(
            ok=False,
            error=(
                f"На {day} в графике никого нет на разборе товара "
                f"(блоки schedule.role: {roles_hint}). Проверьте график или синк в 07:00."
            ),
        )

    employee_names = [
        str(r.get("name", "")).strip()
        for r in employee_rows
        if str(r.get("name", "")).strip()
    ]
    resolved: list[str] = []
    seen: set[str] = set()
    for sched_name, _ in on_shift:
        canonical = match_employee_name(sched_name, employee_names) or sched_name.strip()
        key = canonical.lower()
        if key and key not in seen:
            seen.add(key)
            resolved.append(canonical)

    if len(resolved) == 1:
        return EmployeeResolveResult(
            ok=True,
            canonical_name=resolved[0],
            matched_by="schedule_unpacking",
        )

    if len(resolved) > 1:
        return EmployeeResolveResult(
            ok=False,
            error=(
                f"На {day} на разборе товара в графике несколько человек: "
                f"{', '.join(resolved)}. Укажите имя явно."
            ),
            candidates=resolved,
        )

    sched_only = ", ".join(e for e, _ in on_shift)
    return EmployeeResolveResult(
        ok=False,
        error=(
            f"В графике на {day}: {sched_only}, но имена не найдены в employees. "
            "Добавьте сотрудников в справочник с теми же именами."
        ),
        candidates=[e for e, _ in on_shift],
    )
