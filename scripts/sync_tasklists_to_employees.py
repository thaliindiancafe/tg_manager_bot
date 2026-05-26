"""Map Google Tasks list titles to employees.google_tasks_id (personal Gmail OAuth)."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.google.tasklist_employees_sync import sync_tasklists_to_employees_sheet


def _configure_stdout() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")


async def _run(apply: bool) -> int:
    _configure_stdout()

    if not apply:
        print("(dry-run) Для записи запустите с --apply\n")

    result = await sync_tasklists_to_employees_sheet(
        apply=apply,
        auto_register_from_lists=True,
    )
    if result.get("skipped"):
        print("❌ OAuth не настроен. Сначала:")
        print("   python scripts/google_tasks_oauth_setup.py\n")
        return 1
    if not result.get("ok"):
        print(f"❌ Ошибка: {result.get('error', 'unknown')}\n")
        return 1

    print(f"✅ Обновлено строк employees: {result.get('updated', 0)}")
    print(f"✅ Новых строк из списков Tasks: {result.get('created', 0)}")
    unmatched = result.get("unmatched_employees") or []
    if unmatched:
        print("\nСотрудники без списка Tasks (имя не совпало):")
        for name in unmatched:
            print(f"  • {name}")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Привязать колонки Google Tasks к employees.google_tasks_id",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Записать google_tasks_id в таблицу бота",
    )
    args = parser.parse_args()
    raise SystemExit(asyncio.run(_run(apply=args.apply)))


if __name__ == "__main__":
    main()
