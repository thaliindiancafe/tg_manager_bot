"""Parse employee lines from chat for bulk register."""

from __future__ import annotations

import re
from dataclasses import dataclass

# Strip Telegram forward metadata
_NOISE_RE = re.compile(
    r"^\s*\[\d{2}\.\d{2}\.\d{4}\s+\d{2}:\d{2}\].*$|"
    r"^\s*Валентина.*$|"
    r"^\s*внеси\s+.*базу.*$",
    re.IGNORECASE,
)
_NUMBER_PREFIX_RE = re.compile(r"^\s*\d+[\.\)]\s*")
_AT_USERNAME_RE = re.compile(r"@([a-zA-Z][a-zA-Z0-9_]{2,})")
_TRAILING_TAIL_RE = re.compile(r"\s*[-–—]\s*(?P<tail>\S+)\s*$")


@dataclass
class ParsedEmployeeLine:
    name: str
    role: str
    username: str


def _normalize_username(value: str) -> str:
    u = (value or "").strip().lstrip("@")
    if u and re.match(r"^[a-zA-Z][a-zA-Z0-9_]{2,}$", u):
        return u
    return ""


def _trim_trailing_dashes(value: str) -> str:
    return re.sub(r"[\s\-–—]+$", "", (value or "").strip()).strip()


def parse_employee_line(line: str) -> ParsedEmployeeLine | None:
    """
    Parse one line, flexible dashes and @username placement.

    Examples:
    - «Ирина, менеджер - @irina_kovaleva_l»
    - «Пракаш, шеф повар - Пракаш»
    - «Мохит, уборщик- Mohit»
    - «Гири, су- шеф- @Giri5719»
    """
    raw = (line or "").strip()
    if not raw or _NOISE_RE.match(raw):
        return None
    raw = _NUMBER_PREFIX_RE.sub("", raw).strip()
    if not raw or len(raw) < 2:
        return None

    username = ""
    at_match = _AT_USERNAME_RE.search(raw)
    if at_match:
        username = at_match.group(1)
        raw = (raw[: at_match.start()] + raw[at_match.end() :]).strip()
        raw = _trim_trailing_dashes(raw)

    if "," not in raw:
        name = raw.strip()
        return ParsedEmployeeLine(name=name, role="", username=username) if name else None

    name, rest = raw.split(",", 1)
    name = name.strip()
    rest = rest.strip()
    role = rest

    if not username:
        trail = _TRAILING_TAIL_RE.search(rest)
        if trail:
            tail = trail.group("tail")
            u = _normalize_username(tail)
            if u:
                username = u
            role = rest[: trail.start()].strip()

    role = _trim_trailing_dashes(role)

    if not name:
        return None

    return ParsedEmployeeLine(name=name, role=role, username=username)


def parse_employees_bulk_text(text: str) -> list[ParsedEmployeeLine]:
    """Extract employee entries from multiline pasted message."""
    seen: set[tuple[str, str]] = set()
    out: list[ParsedEmployeeLine] = []

    for line in (text or "").splitlines():
        parsed = parse_employee_line(line)
        if parsed is None:
            continue
        key = (parsed.name.lower(), parsed.username.lower())
        if key in seen:
            continue
        seen.add(key)
        out.append(parsed)

    return out
