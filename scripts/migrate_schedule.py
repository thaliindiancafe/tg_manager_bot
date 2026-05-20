"""Migrate schedule from client spreadsheet to bot spreadsheet (one-shot CLI)."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pprint import pprint
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.config import settings
from src.google import sheets
from src.utils.schedule_parser import parse_schedule_grid

TARGET_SHEET = "schedule"


def _configure_stdout() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")


def _print_raw_sheet_debug(sheet_title: str, values: list[list[Any]]) -> None:
    print("\n=== DEBUG: сырые данные листа (перед парсингом) ===\n")
    print(f"1) Название листа: {sheet_title!r}\n")
    print("2) Первые 30 строк (как из Google Sheets API, без обработки):\n")
    pprint(values[:30], width=200)
    print()


async def _print_source_tabs() -> None:
    titles = await sheets.list_source_sheet_titles()
    print("Листы в исходной таблице:\n")
    for idx, name in enumerate(titles, start=1):
        print(f"  {idx}. {name}")
    print()


async def _read_values_for_migrate() -> tuple[str, list[list[Any]]]:
    await _print_source_tabs()
    sheet_title = settings.source_schedule_sheet_name
    values = await sheets.read_source_sheet_values(sheet_title)
    return sheet_title, values


def test_parse(rows: list[dict[str, str]] | None = None) -> list[dict[str, str]]:
    if rows is None:
        sheet_title, values = asyncio.run(_read_values_for_migrate())
        _print_raw_sheet_debug(sheet_title, values)
        now = datetime.now(ZoneInfo(settings.timezone))
        rows = parse_schedule_grid(values, now)

    print(f"Распознано смен: {len(rows)}\n")
    if not rows:
        print("⚠️ Смены не найдены")
        return rows

    print("Первые 20 строк:\n")
    for row in rows[:20]:
        print(
            f"  {row['date']} | {row['role']} | {row['employee']} | "
            f"{row['shift_start']}-{row['shift_end']}"
        )
    print()
    return rows


async def _migrate_async() -> None:
    print("Миграция графика из старой таблицы...\n")
    sheet_title, values = await _read_values_for_migrate()
    _print_raw_sheet_debug(sheet_title, values)
    if not values:
        print(f"❌ Лист «{sheet_title}» пуст или недоступен")
        return

    now = datetime.now(ZoneInfo(settings.timezone))
    rows = parse_schedule_grid(values, now)
    if not rows:
        print("❌ Не удалось распознать смены")
        return

    await sheets.replace_schedule_rows(rows)
    print(f"✅ Записано {len(rows)} смен в лист «{TARGET_SHEET}»\n")
    test_parse(rows)


def migrate() -> None:
    asyncio.run(_migrate_async())


def main() -> None:
    _configure_stdout()
    parser = argparse.ArgumentParser(description="Migrate schedule from client sheet")
    parser.add_argument(
        "--test",
        action="store_true",
        help="Only parse and print first 20 rows without writing",
    )
    args = parser.parse_args()

    if args.test:

        async def _test() -> None:
            sheet_title, values = await _read_values_for_migrate()
            _print_raw_sheet_debug(sheet_title, values)
            now = datetime.now(ZoneInfo(settings.timezone))
            test_parse(parse_schedule_grid(values, now))

        asyncio.run(_test())
        return

    migrate()


if __name__ == "__main__":
    main()
