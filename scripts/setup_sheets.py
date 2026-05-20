"""Create Google Sheets tabs/headers and test source schedule parsing."""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.config import settings
from src.google import sheets
from src.google.sheets import SHEET_HEADERS, init_all_sheets
from src.utils.schedule_parser import parse_schedule_grid


def _configure_stdout() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")


def _print_sheet_status(sheet_name: str, status: str) -> None:
    if status == "created":
        print(f"✅ Лист {sheet_name} создан")
    elif status in {"ok", "skipped"}:
        print(f"⏭ Лист {sheet_name} уже существует")
    elif status == "updated":
        print(f"✅ Лист {sheet_name} обновлён")
    else:
        print(f"ℹ️ Лист {sheet_name}: {status}")


async def setup_sheets() -> None:
    """Create missing tabs and header rows in SPREADSHEET_ID."""
    print("Настройка листов в таблице бота...\n")
    results = await init_all_sheets(force=False)
    for sheet_name in sorted(SHEET_HEADERS):
        _print_sheet_status(sheet_name, results.get(sheet_name, "unknown"))
    print()


async def test_schedule_parse() -> None:
    """
    Read SOURCE_SPREADSHEET_ID, parse current month from SOURCE_SCHEDULE_SHEET_NAME,
    print first 10 parsed rows.
    """
    sheet_title = settings.source_schedule_sheet_name
    print(
        f"Тест парсинга графика из старой таблицы (лист «{sheet_title}»)...\n"
    )

    try:
        titles = await sheets.list_source_sheet_titles()
        if sheet_title not in titles:
            print(f"❌ Лист «{sheet_title}» не найден в исходной таблице.")
            print("Доступные вкладки:")
            for name in titles:
                print(f"  • {name}")
            print(
                "\nУкажите точное имя вкладки в .env: SOURCE_SCHEDULE_SHEET_NAME=...\n"
            )
            return

        values = await sheets.read_source_sheet_values(sheet_title)
        if not values:
            print(f"❌ Лист «{sheet_title}» пуст")
            return

        now = datetime.now(ZoneInfo(settings.timezone))
        parsed = parse_schedule_grid(values, now)
        if not parsed:
            month = now.strftime("%m.%Y")
            print(
                f"⚠️ Не найдены смены за текущий месяц ({month}) в листе «{sheet_title}»"
            )
            return

        print(f"Найдено смен: {len(parsed)}. Первые 10 строк:\n")
        for row in parsed[:10]:
            print(
                f"  {row['date']} | {row['employee']} | {row.get('role', '')} | "
                f"{row['shift_start']}-{row['shift_end']}"
            )
        print()
    except Exception as exc:
        print(f"❌ Ошибка парсинга графика: {exc}\n")


async def main() -> None:
    _configure_stdout()
    await setup_sheets()
    await test_schedule_parse()


if __name__ == "__main__":
    asyncio.run(main())
