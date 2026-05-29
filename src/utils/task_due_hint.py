"""Parse due-date hints from Russian task phrases."""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

_DATE_DMY = re.compile(r"\b(\d{1,2})\.(\d{1,2})(?:\.(\d{2,4}))?\b")


def parse_due_date_hint(text: str, *, tz_name: str) -> str:
    """
    Return YYYY-MM-DD or empty string if no hint found.
    Handles: сегодня, завтра, послезавтра, DD.MM[.YYYY].
    """
    low = (text or "").lower()
    tz = ZoneInfo(tz_name)
    today = datetime.now(tz).date()

    if re.search(r"\bпослезавтра\b", low):
        return (today + timedelta(days=2)).isoformat()
    if re.search(r"\bзавтра\b", low):
        return (today + timedelta(days=1)).isoformat()
    if re.search(r"\bсегодня\b", low):
        return today.isoformat()

    match = _DATE_DMY.search(text or "")
    if match:
        day, month = int(match.group(1)), int(match.group(2))
        year_raw = match.group(3)
        year = int(year_raw) if year_raw else today.year
        if year_raw and len(year_raw) == 2:
            year = 2000 + int(year_raw)
        try:
            return date(year, month, day).isoformat()
        except ValueError:
            return ""

    return ""
