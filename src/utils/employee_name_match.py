"""Match employee names from natural Russian phrases (cases, «у Иры»)."""

from __future__ import annotations

# Разговорные формы → имя в employees.name (дополняется из списка сотрудников)
NAME_ALIASES: dict[str, str] = {
    "ирина": "ира",
    "ирины": "ира",
    "ирине": "ира",
    "ирину": "ира",
}


def strip_employee_name_prefixes(name: str) -> str:
    """Remove common Russian prepositions before a name."""
    text = name.strip().lower()
    for prefix in ("у ", "для ", "к ", "от ", "со ", "с "):
        if text.startswith(prefix):
            text = text[len(prefix) :].strip()
            break
    return text


def inflected_name_forms(canonical: str) -> list[str]:
    """
    Generate common Russian case forms for a first name (Ира → иры, ире…).

    Not a full morphological analyzer — covers typical staff first names in -а/-я.
    """
    base = strip_employee_name_prefixes(canonical)
    if not base or len(base) < 2:
        return []

    out: list[str] = []
    seen: set[str] = set()

    def add(value: str) -> None:
        key = value.strip().lower()
        if key and key not in seen:
            seen.add(key)
            out.append(key)

    add(base)

    if base.endswith("а") and len(base) >= 3:
        stem = base[:-1]
        add(stem + "ы")
        add(stem + "е")
        add(stem + "у")
        add(stem + "ой")
        add(stem + "е")
    elif base.endswith("я") and len(base) >= 3:
        stem = base[:-1]
        add(stem + "и")
        add(stem + "е")
        add(stem + "ю")
        add(stem + "ей")
        add(stem + "и")
    elif base.endswith("й") and len(base) >= 3:
        stem = base[:-1]
        add(stem + "я")
        add(stem + "ю")
        add(stem + "е")
    elif base.endswith("ь") and len(base) >= 3:
        stem = base[:-1]
        add(stem + "я")
        add(stem + "ю")
        add(stem + "и")

    return out


def build_name_lookup(employee_names: list[str]) -> dict[str, str]:
    """Map lowered inflected form → canonical employees.name."""
    lookup: dict[str, str] = {}
    for name in employee_names:
        canonical = str(name).strip()
        if not canonical:
            continue
        for form in inflected_name_forms(canonical):
            lookup[form] = canonical
            alias = NAME_ALIASES.get(form)
            if alias and alias not in lookup:
                lookup[alias] = canonical
        lookup[canonical.strip().lower()] = canonical
    return lookup


def nominative_candidates(name: str) -> list[str]:
    """
    Guess nominative forms from genitive/dative/etc. (Иры → Ира, Ире → Ира).

    Not full morphology — covers typical restaurant staff first names.
    """
    base = strip_employee_name_prefixes(name)
    if not base:
        return []

    out: list[str] = []
    seen: set[str] = set()

    def add(value: str) -> None:
        key = value.strip().lower()
        if key and key not in seen:
            seen.add(key)
            out.append(key)

    add(base)

    if len(base) >= 3:
        if base.endswith("ы"):
            add(base[:-1] + "а")
        if base.endswith("е"):
            add(base[:-1] + "а")
        if base.endswith("и"):
            add(base[:-1] + "я")
            add(base[:-1] + "а")
        if base.endswith("ю"):
            add(base[:-1] + "я")
            add(base[:-1] + "а")
        if base.endswith("ой"):
            add(base[:-2] + "а")
        if base.endswith("ей"):
            add(base[:-2] + "я")
            add(base[:-2] + "а")

    return out


def match_employee_name(
    query: str,
    employee_names: list[str],
    *,
    lookup: dict[str, str] | None = None,
) -> str | None:
    """
    Return canonical name from employees list that matches query, or None.

    employee_names — values from employees.name column.
    """
    if not query.strip():
        return None

    by_lower = {n.strip().lower(): n.strip() for n in employee_names if str(n).strip()}
    name_lookup = lookup or build_name_lookup(employee_names)

    for candidate in nominative_candidates(query):
        alias = NAME_ALIASES.get(candidate, candidate)
        if alias in name_lookup:
            return name_lookup[alias]
        if candidate in name_lookup:
            return name_lookup[candidate]
        if alias in by_lower:
            return by_lower[alias]
        if candidate in by_lower:
            return by_lower[candidate]

    return None
