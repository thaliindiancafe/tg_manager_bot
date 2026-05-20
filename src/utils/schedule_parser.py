"""Parse client schedule grid (SOURCE spreadsheet) into flat shift rows."""

from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from src.config import settings

logger = logging.getLogger(__name__)

DEFAULT_ROLE_CATEGORIES: dict[str, str] = {
    "manager": "Manager",
    "barista": "Barista",
    "waiters": "Waiters",
    "kleener": "Kleener",
    "chefs": "Chefs",
}


def _role_categories() -> dict[str, str]:
    """Lowercased block header -> display role. Extend via SCHEDULE_ROLE_CATEGORY_ALIASES_JSON."""
    merged: dict[str, str] = dict(DEFAULT_ROLE_CATEGORIES)
    raw = (getattr(settings, "schedule_role_category_aliases_json", "") or "").strip()
    if not raw:
        return merged
    try:
        extra = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning(
            "SCHEDULE_ROLE_CATEGORY_ALIASES_JSON is not valid JSON, using defaults only",
        )
        return merged
    if not isinstance(extra, dict):
        return merged
    for k, v in extra.items():
        if isinstance(k, str) and isinstance(v, str) and k.strip() and v.strip():
            merged[k.strip().lower()] = v.strip()
    return merged

MONTH_MARKERS = (
    "январ",
    "феврал",
    "март",
    "апрел",
    "май",
    "июн",
    "июл",
    "август",
    "сентябр",
    "октябр",
    "ноябр",
    "декабр",
)

SKIP_CELL_TEXT = {
    "0",
    "-",
    "в",
    "выход",
    "отъехала",
    "индия",
    "болен",
    "екатеренбург",
    "беларусь",
    "отпр",
    "отпуск",
}

# Header cells may be formatted as DD.MM., DD.MM.YYYY, US MM/DD, or a Sheets serial number.
DATE_HEADER_DD_MM_YYYY_RE = re.compile(
    r"^(\d{1,2})[.\-/](\d{1,2})(?:[.\-/](\d{2,4}))?\.*\s*$",
    re.IGNORECASE,
)
DATE_HEADER_SLASH_US_RE = re.compile(
    r"^(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\s*$",
    re.IGNORECASE,
)
DAY_ONLY_HEADER_RE = re.compile(r"^(\d{1,2})\.?$", re.IGNORECASE)
# Fallback: short DD.MM (no trailing year)
DATE_HEADER_DD_MM_ONLY_RE = re.compile(r"^(\d{1,2})[.\-/](\d{1,2})\.?\s*$", re.IGNORECASE)

# Serial date compatible with Sheets/Excel day count from 1899-12-30.
_SHEETS_SERIAL_ORIGIN = date(1899, 12, 30)


def _date_from_sheet_serial(serial: float) -> date | None:
    """Convert Google Sheets numeric date (days since ~1899-12-30) to calendar date."""
    if serial < 1 or serial > 700_000:
        return None
    try:
        d = _SHEETS_SERIAL_ORIGIN + timedelta(days=float(serial))
    except OverflowError:
        return None
    if d.year < 1900 or d.year > 2100:
        return None
    return d

# Month labels at start of a cell (word boundary after stem avoids "мартин" → март).
_MONTH_TITLE_PATTERNS: list[tuple[re.Pattern[str], int]] = [
    (re.compile(r"^\s*январ(я|ь)\b", re.IGNORECASE), 1),
    (re.compile(r"^\s*феврал(я|ь)\b", re.IGNORECASE), 2),
    (re.compile(r"^\s*марта?\b", re.IGNORECASE), 3),
    (re.compile(r"^\s*апрел(я|ь)\b", re.IGNORECASE), 4),
    (re.compile(r"^\s*ма(й|я|е|ю)\b", re.IGNORECASE), 5),
    (re.compile(r"^\s*июн(я|ь)\b", re.IGNORECASE), 6),
    (re.compile(r"^\s*июл(я|ь)\b", re.IGNORECASE), 7),
    (re.compile(r"^\s*август\b", re.IGNORECASE), 8),
    (re.compile(r"^\s*сентябр(я|ь)\b", re.IGNORECASE), 9),
    (re.compile(r"^\s*октябр(я|ь)\b", re.IGNORECASE), 10),
    (re.compile(r"^\s*ноябр(я|ь)\b", re.IGNORECASE), 11),
    (re.compile(r"^\s*декабр(я|ь)\b", re.IGNORECASE), 12),
]


def _year_from_now(now: datetime) -> int:
    """Calendar year of *now* in app timezone (used for all parsed header dates)."""
    tz = ZoneInfo(settings.timezone)
    now_local = now.astimezone(tz) if now.tzinfo else now.replace(tzinfo=tz)
    return now_local.year


# At least this many columns must parse as dates for a row to count as the date header.
_DATE_HEADER_MIN_COLUMNS = 5

# Month banner: ignore merged-cell repetition (same text across many cols). Employee rows have
# many distinct cells in the row; merged "Май" across Z columns still has low distinct count.
_MONTH_BANNER_MAX_DISTINCT_IN_SCAN = 22
_MONTH_BANNER_SCAN_COLS = 40


def _row_non_empty_cell_count(row: list[Any]) -> int:
    return sum(1 for c in row if c is not None and str(c).strip())


def _row_distinct_non_empty_prefix(row: list[Any], max_cols: int) -> int:
    seen: set[str] = set()
    for c in row[:max_cols]:
        if c is None:
            continue
        t = str(c).strip()
        if t:
            seen.add(t.casefold())
    return len(seen)


def _month_title_from_row_leading(row: list[Any], now: datetime) -> int | None:
    """Month 1–12 from column A, then B if A is empty.

    Handles: (1) merged month-only row (few distinct cells); (2) row «Сентябрь» + «01.09.»…
    Rejects dense employee rows where A looks like a month name (e.g. rare names).
    """
    if not row:
        return None
    a0 = str(row[0]).strip() if row[0] is not None else ""
    if a0:
        if _normalize_category(a0) is not None:
            return None
        m = _month_from_cell_text(a0)
        if m is not None:
            dcount = _row_distinct_non_empty_prefix(row, _MONTH_BANNER_SCAN_COLS)
            if dcount <= _MONTH_BANNER_MAX_DISTINCT_IN_SCAN:
                return m
            if _date_header_tail_parse_count(row, m, now) >= _DATE_HEADER_MIN_COLUMNS:
                return m
            return None
    prefix = row[:15]
    if _row_non_empty_cell_count(prefix) > 10:
        return None
    for idx in (1,):
        if idx >= len(row):
            break
        cell = row[idx]
        if cell is None:
            continue
        s = str(cell).strip()
        if not s:
            continue
        m = _month_from_cell_text(s)
        if m is not None:
            return m
    return None


def _slice_values_for_current_calendar_month(
    values: list[list[Any]],
    now: datetime,
) -> list[list[Any]]:
    """Keep only rows from the header of the current calendar month until another month starts."""
    tz = ZoneInfo(settings.timezone)
    now_local = now.astimezone(tz) if now.tzinfo else now.replace(tzinfo=tz)
    target = now_local.month

    start: int | None = None
    for i, row in enumerate(values):
        if not row:
            continue
        if _month_title_from_row_leading(row, now_local) == target:
            start = i
            break

    if start is None:
        return []

    end = len(values)
    for j in range(start + 1, len(values)):
        row = values[j]
        if not row:
            continue
        m = _month_title_from_row_leading(row, now_local)
        if m is not None and m != target:
            end = j
            break

    return values[start:end]


def _normalize_category(name: str) -> str | None:
    cleaned = name.strip().lower().rstrip(":").strip()
    return _role_categories().get(cleaned)


def _skip_non_employee_first_cell(text: str) -> bool:
    """Rows that must not be parsed as employee names (first column)."""
    s = text.strip()
    if not s:
        return True
    low = s.lower()
    if "день с опозданием" in low:
        return True
    if "lection" in low:
        return True
    if "need to make" in low:
        return True
    if re.fullmatch(r"[\d\-\s;:,./]+", s):
        return True
    return False


def _date_header_first_column_ok(row: list[Any]) -> bool:
    """First column must not be a role header or schedule-noise row."""
    if not row:
        return False
    a = str(row[0]).strip() if row[0] is not None else ""
    if not a:
        return True
    if _normalize_category(a) is not None:
        return False
    if _skip_non_employee_first_cell(a):
        return False
    return True


def _is_month_title_row(row: list[Any], now: datetime) -> bool:
    return _month_title_from_row_leading(row, now) is not None


def _month_from_cell_text(text: str) -> int | None:
    """Detect calendar month from a single cell (strict prefix / month-word patterns)."""
    raw = str(text).strip()
    if not raw:
        return None
    normalized = raw.lower().replace("ё", "е")
    for pattern, month_num in _MONTH_TITLE_PATTERNS:
        if pattern.match(normalized):
            return month_num
    # Fallback: short substring markers (legacy), only for compact labels
    if len(normalized) > 40:
        return None
    for idx, marker in enumerate(MONTH_MARKERS):
        if re.search(
            rf"(?<![а-яё]){re.escape(marker)}(?![а-яё])",
            normalized,
            re.IGNORECASE,
        ):
            return idx + 1
    return None


def _month_from_russian_title_row(row: list[Any], now: datetime) -> int | None:
    """Backward-compatible alias: month title only from columns A/B."""
    return _month_title_from_row_leading(row, now)


def _normalize_two_digit_year(y: str, now: datetime) -> int:
    n = int(y)
    tz = ZoneInfo(settings.timezone)
    now_local = now.astimezone(tz) if now.tzinfo else now.replace(tzinfo=tz)
    cy = now_local.year
    century = cy - cy % 100
    cand = century + n
    if cand < cy - 50:
        return cand + 100
    if cand > cy + 50:
        return cand - 100
    return cand


def _calendar_from_header_cell(
    cell_raw: Any,
    table_month: int | None,
    now: datetime,
) -> date | None:
    """Interpret one header cell value as calendar date."""
    tz = ZoneInfo(settings.timezone)
    now_local = now.astimezone(tz) if now.tzinfo else now.replace(tzinfo=tz)

    if isinstance(cell_raw, (int, float)) and not isinstance(cell_raw, bool):
        d = _date_from_sheet_serial(float(cell_raw))
        if d is not None:
            return d

    text = str(cell_raw).strip()
    if not text:
        return None

    slash = DATE_HEADER_SLASH_US_RE.match(text)
    dd = DATE_HEADER_DD_MM_YYYY_RE.match(text)
    dm = DATE_HEADER_DD_MM_ONLY_RE.match(text) if dd is None else None

    if slash:
        a, b = int(slash.group(1)), int(slash.group(2))
        y_raw = slash.group(3)
        if y_raw:
            yi = (
                _normalize_two_digit_year(y_raw, now)
                if len(y_raw) == 2
                else int(y_raw)
            )
            month, day = (a, b)
            try:
                return date(yi, month, day)
            except ValueError:
                pass
            month, day = (b, a)
            try:
                return date(yi, month, day)
            except ValueError:
                return None

        low, high = (a, b) if a <= b else (b, a)
        maybe_us = maybe_eu = None
        if 1 <= high <= 12 and 1 <= low <= 31:
            try:
                maybe_us = date(now_local.year, high, low)
            except ValueError:
                pass
            try:
                maybe_eu = date(now_local.year, low, high)
            except ValueError:
                pass
        if maybe_us is not None:
            delta_us = abs((maybe_us - now_local.date()).days)
            if maybe_eu is not None:
                delta_eu = abs((maybe_eu - now_local.date()).days)
                return maybe_eu if delta_eu < delta_us else maybe_us
            return maybe_us
        return maybe_eu

    if dd:
        day = int(dd.group(1))
        mm = int(dd.group(2))
        y_raw = dd.group(3)
        if table_month is not None:
            mm = table_month
        if y_raw:
            year = (
                _normalize_two_digit_year(y_raw, now)
                if len(y_raw) == 2
                else int(y_raw)
            )
        else:
            year = _year_from_now(now)
        try:
            return date(year, mm, day)
        except ValueError:
            return None

    if dm:
        day = int(dm.group(1))
        mm = int(dm.group(2))
        if table_month is not None:
            mm = table_month
        try:
            return date(_year_from_now(now), mm, day)
        except ValueError:
            return None

    numeric_match = re.match(r"^-?(\d+\.?\d*)\s*$", text.replace(",", "."))
    if numeric_match and re.search(r"[eE]", text) is None:
        try:
            ser = float(numeric_match.group(1))
            d = _date_from_sheet_serial(ser)
            if d is not None:
                return d
        except ValueError:
            pass

    if table_month is not None:
        m1 = DAY_ONLY_HEADER_RE.match(text)
        if m1:
            day = int(m1.group(1))
            try:
                return date(_year_from_now(now), table_month, day)
            except ValueError:
                return None

    return None


def _date_header_tail_parse_count(row: list[Any], lead_month: int, now: datetime) -> int:
    """How many cells in row[1:] parse as calendar dates when table month is lead_month."""
    n = 0
    for col_idx in range(1, min(len(row), 50)):
        if _calendar_from_header_cell(row[col_idx], lead_month, now) is not None:
            n += 1
    return n


def _parse_shift_cell(cell: str) -> tuple[str, str] | None:
    value = cell.strip()
    if not value:
        return None

    normalized_text = value.lower().replace("ё", "е")
    if normalized_text in SKIP_CELL_TEXT:
        return None

    if not re.search(r"\d", value):
        return None

    normalized = value.lower()
    for char in ";.":
        normalized = normalized.replace(char, ":")
    normalized = re.sub(r"\s+", "", normalized)
    normalized = re.sub(r":+", ":", normalized)

    match = re.match(r"^(\d{1,2}):(\d{2})-(\d{1,2}):(\d{2})$", normalized)
    if not match:
        return None

    shift_start = f"{int(match.group(1)):02d}:{match.group(2)}"
    shift_end = f"{int(match.group(3)):02d}:{match.group(4)}"
    return shift_start, shift_end


def _find_date_columns(
    row: list[Any],
    table_month: int | None,
    now: datetime,
) -> dict[int, str]:
    """Build column index -> ISO date (YYYY-MM-DD)."""
    day_by_col: dict[int, str] = {}

    for col_idx, cell in enumerate(row):
        if col_idx == 0:
            continue
        d = _calendar_from_header_cell(cell, table_month, now)
        if d is None:
            continue

        if isinstance(cell, (int, float)) and not isinstance(cell, bool):
            out = d
        elif table_month is not None:
            try:
                out = date(_year_from_now(now), table_month, d.day)
            except ValueError:
                continue
        else:
            out = d

        day_by_col[col_idx] = out.strftime("%Y-%m-%d")

    if len(day_by_col) < _DATE_HEADER_MIN_COLUMNS:
        return {}
    return day_by_col


def parse_schedule_grid(
    values: list[list[Any]],
    now: datetime | None = None,
) -> list[dict[str, str]]:
    """Parse shifts for the current calendar month from raw grid rows (Sheets API values)."""
    if now is None:
        now = datetime.now(ZoneInfo(settings.timezone))
    values = _slice_values_for_current_calendar_month(values, now)
    if not values:
        return []

    parsed: list[dict[str, str]] = []
    current_role = ""
    table_month: int | None = None
    day_by_col: dict[int, str] = {}

    for row in values:
        if not row:
            continue

        if _is_month_title_row(row, now):
            m = _month_title_from_row_leading(row, now)
            if m is not None:
                table_month = m
            day_by_col = {}
            continue

        new_map = _find_date_columns(row, table_month, now)
        if new_map and _date_header_first_column_ok(row):
            day_by_col = new_map
            continue

        first_cell = str(row[0]).strip() if row else ""
        if not first_cell:
            continue

        category_header = _normalize_category(first_cell)
        if category_header is not None:
            current_role = category_header
            continue

        if _skip_non_employee_first_cell(first_cell):
            continue

        if not day_by_col:
            continue

        employee = first_cell
        role = current_role or first_cell

        for col_idx, date_iso in day_by_col.items():
            if col_idx >= len(row):
                continue
            shift = _parse_shift_cell(str(row[col_idx]))
            if shift is None:
                continue
            shift_start, shift_end = shift
            parsed.append(
                {
                    "date": date_iso,
                    "employee": employee,
                    "role": role,
                    "shift_start": shift_start,
                    "shift_end": shift_end,
                    "telegram_user_id": "",
                }
            )

    return parsed
