"""Resolve employee by name, @username, or job role (RU/EN aliases)."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from src.config import settings
from src.utils.employee_name_match import match_employee_name

# canonical role key -> list of aliases (lowercase, normalized before compare)
_DEFAULT_ROLE_ALIASES: dict[str, list[str]] = {
    "chef": [
        "chef",
        "chefs",
        "шеф",
        "шефповар",
        "шефповара",
        "шефповар",
        "headchef",
        "executivechef",
    ],
    "sous_chef": [
        "souschef",
        "sous-chef",
        "sous chef",
        "су-шеф",
        "сушеф",
        "су шеф",
    ],
    "manager": ["manager", "менеджер", "управляющий", "управляющая"],
    "barista": ["barista", "бариста", "баристы"],
    "waiter": ["waiter", "waiters", "официант", "официанты", "официантка"],
    "hostess": ["hostess", "хостес", "хостесс"],
    "assistant": [
        "assistant",
        "помощник",
        "помощница",
    ],
}

_ROLE_LABELS_RU: dict[str, str] = {
    "chef": "шеф",
    "sous_chef": "су-шеф",
    "manager": "менеджер",
    "barista": "бариста",
    "waiter": "официант",
    "hostess": "хостес",
    "assistant": "помощник",
}


def _normalize_role_token(text: str) -> str:
    """Lowercase, collapse spaces/hyphens for alias lookup."""
    t = (text or "").strip().lower()
    t = re.sub(r"[@«»\"']", "", t)
    t = re.sub(r"[\s\-_]+", "", t)
    return t


def _load_role_alias_map() -> dict[str, str]:
    """alias_token -> canonical role key."""
    out: dict[str, str] = {}
    for canonical, aliases in _DEFAULT_ROLE_ALIASES.items():
        for alias in aliases:
            out[_normalize_role_token(alias)] = canonical
    raw = (getattr(settings, "employee_role_aliases_json", "") or "").strip()
    if raw:
        try:
            extra = json.loads(raw)
            if isinstance(extra, dict):
                for canonical, aliases in extra.items():
                    key = str(canonical).strip().lower()
                    if not key:
                        continue
                    items = aliases if isinstance(aliases, list) else [aliases]
                    for alias in items:
                        out[_normalize_role_token(str(alias))] = key
        except json.JSONDecodeError:
            pass
    return out


def role_to_canonical(role_label: str) -> str | None:
    """Map free-text role from employees.role or user phrase to canonical key."""
    token = _normalize_role_token(role_label)
    if not token:
        return None
    alias_map = _load_role_alias_map()
    if token in alias_map:
        return alias_map[token]
    # partial: «сушефу» -> сушеф
    for alias, canonical in alias_map.items():
        if len(alias) >= 4 and (token.startswith(alias) or alias.startswith(token)):
            return canonical
    return None


def canonical_role_label_ru(canonical: str) -> str:
    return _ROLE_LABELS_RU.get(canonical, canonical.replace("_", " "))


@dataclass
class EmployeeResolveResult:
    ok: bool
    canonical_name: str = ""
    matched_by: str = ""  # name | username | role
    error: str = ""
    candidates: list[str] | None = None


def _is_active_row(row: dict[str, Any]) -> bool:
    active = str(row.get("active", "true")).strip().lower()
    return active not in {"false", "0", "no", "нет"}


def _rows_by_role_canonical(
    rows: list[dict[str, Any]], canonical: str
) -> list[dict[str, Any]]:
    matched: list[dict[str, Any]] = []
    for row in rows:
        if not _is_active_row(row):
            continue
        cell = str(row.get("role", "")).strip()
        if not cell:
            continue
        if role_to_canonical(cell) == canonical:
            matched.append(row)
    return matched


def extract_usernames_from_text(text: str) -> list[str]:
    """All @username tokens in a phrase (e.g. «Асим менеджер @asimhayatkhan»)."""
    found: list[str] = []
    for match in re.finditer(r"@([a-zA-Z][a-zA-Z0-9_]{4,})", text or ""):
        u = match.group(1).lower()
        if u not in found:
            found.append(u)
    return found


def _resolve_by_username(
    username: str,
    active_rows: list[dict[str, Any]],
) -> EmployeeResolveResult | None:
    u = username.lstrip("@").lower()
    by_user: list[dict[str, Any]] = []
    for row in active_rows:
        cell = str(row.get("username", "")).strip().lstrip("@").lower()
        if cell == u:
            by_user.append(row)
    if len(by_user) == 1:
        name = str(by_user[0].get("name", "")).strip()
        return EmployeeResolveResult(ok=True, canonical_name=name, matched_by="username")
    if len(by_user) > 1:
        names = [str(r.get("name", "")).strip() for r in by_user]
        return EmployeeResolveResult(
            ok=False,
            error=f"Несколько сотрудников с username @{u}: {', '.join(names)}",
            candidates=names,
        )
    return None


def resolve_employee_reference(
    query: str,
    rows: list[dict[str, Any]],
) -> EmployeeResolveResult:
    """
    Resolve query to employees.name.

    Order: @username in text -> @username -> exact name -> role alias -> fuzzy name.
    """
    q = (query or "").strip()
    if not q:
        return EmployeeResolveResult(ok=False, error="Пустой идентификатор сотрудника")

    active_rows = [r for r in rows if _is_active_row(r)]

    for embedded in extract_usernames_from_text(q):
        by_embedded = _resolve_by_username(embedded, active_rows)
        if by_embedded is not None:
            return by_embedded

    # @username
    if q.startswith("@") or re.match(r"^[a-zA-Z][a-zA-Z0-9_]{4,}$", q.lstrip("@")):
        by_user = _resolve_by_username(q, active_rows)
        if by_user is not None:
            return by_user

    # role (су-шеф, шеф, …)
    canonical = role_to_canonical(q)
    if canonical:
        by_role = _rows_by_role_canonical(active_rows, canonical)
        if len(by_role) == 1:
            name = str(by_role[0].get("name", "")).strip()
            return EmployeeResolveResult(
                ok=True, canonical_name=name, matched_by="role"
            )
        if len(by_role) > 1:
            names = [str(r.get("name", "")).strip() for r in by_role]
            return EmployeeResolveResult(
                ok=False,
                error=(
                    f"Несколько сотрудников с должностью "
                    f"«{canonical_role_label_ru(canonical)}»: {', '.join(names)}. "
                    "Уточните имя."
                ),
                candidates=names,
            )
        return EmployeeResolveResult(
            ok=False,
            error=(
                f"В таблице employees никто не указан с должностью "
                f"«{canonical_role_label_ru(canonical)}». "
                f"Заполните колонку role (например «{canonical_role_label_ru(canonical)}»)."
            ),
        )

    # name
    names = [str(row.get("name", "")).strip() for row in active_rows]
    canonical_name = match_employee_name(q, names)
    target = (canonical_name or q).strip().lower()
    hits = [
        row
        for row in active_rows
        if str(row.get("name", "")).strip().lower() == target
    ]
    if len(hits) == 1:
        name = str(hits[0].get("name", "")).strip()
        return EmployeeResolveResult(ok=True, canonical_name=name, matched_by="name")
    if len(hits) > 1:
        dup = [str(r.get("name", "")).strip() for r in hits]
        return EmployeeResolveResult(
            ok=False,
            error=f"Несколько строк с именем {q!r}",
            candidates=dup,
        )

    return EmployeeResolveResult(
        ok=False,
        error=(
            f"Сотрудник {q!r} не найден. Используйте имя из employees, @username "
            "или должность (шеф, су-шеф, бариста…). Вызовите get_employee_directory."
        ),
    )


def format_staff_directory(rows: list[dict[str, Any]]) -> str:
    """Markdown lines for system prompt."""
    lines: list[str] = []
    for row in rows:
        if not _is_active_row(row):
            continue
        name = str(row.get("name", "")).strip()
        if not name:
            continue
        role_raw = str(row.get("role", "")).strip()
        username = str(row.get("username", "")).strip()
        tid = str(row.get("telegram_user_id", "")).strip()
        parts: list[str] = []
        if role_raw:
            canon = role_to_canonical(role_raw)
            if canon:
                parts.append(f"должность: {role_raw} ({canonical_role_label_ru(canon)})")
            else:
                parts.append(f"должность: {role_raw}")
        if username:
            parts.append(f"@{username.lstrip('@')}")
        parts.append("Telegram: да" if tid else "Telegram: нет (/start)")
        suffix = ", ".join(parts)
        lines.append(f"- **{name}** — {suffix}")
    if not lines:
        return (
            "(нет активных сотрудников; заполните лист employees: name, role, username)"
        )
    return "\n".join(lines)
