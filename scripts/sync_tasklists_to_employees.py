"""Map Google Tasks list titles to employees.google_tasks_id (personal Gmail OAuth)."""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
import unicodedata
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.config import settings
from src.google import sheets
from src.google import tasks as google_tasks
from src.google.oauth_credentials import oauth_configured
from src.google.tasklist_resolve import is_manager_employee_row

_SKIP_LIST_TITLES = frozenset(
    {
        "мои задачи",
        "my tasks",
    }
)


def _configure_stdout() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")


def _normalize_name(value: str) -> str:
    text = unicodedata.normalize("NFKC", value or "")
    text = re.sub(r"[^\w\s]", " ", text, flags=re.UNICODE)
    text = re.sub(r"\s+", " ", text).strip().lower()
    return text


def _match_tasklist(
    employee_name: str,
    tasklists: list[dict[str, str]],
) -> dict[str, str] | None:
    emp_norm = _normalize_name(employee_name)
    if not emp_norm:
        return None

    best: dict[str, str] | None = None
    best_score = 0

    for tl in tasklists:
        title = str(tl.get("title", "")).strip()
        title_norm = _normalize_name(title)
        if not title_norm or title_norm in _SKIP_LIST_TITLES:
            continue

        score = 0
        if emp_norm == title_norm:
            score = 100
        elif emp_norm in title_norm or title_norm in emp_norm:
            score = 80
        elif emp_norm.split()[0] == title_norm.split()[0]:
            score = 60

        if score > best_score:
            best_score = score
            best = tl

    return best if best_score >= 60 else None


async def _run(apply: bool) -> int:
    _configure_stdout()

    if not oauth_configured():
        print("❌ OAuth не настроен. Сначала:")
        print("   python scripts/google_tasks_oauth_setup.py\n")
        return 1

    print("Чтение списков Google Tasks…\n")
    tasklists = await google_tasks.list_tasklists()
    if not tasklists:
        print("❌ Списков задач не найдено (пустой аккаунт или неверный токен).\n")
        return 1

    print("Списки в аккаунте:")
    for tl in tasklists:
        print(f"  • {tl.get('title', '?')}  →  {tl.get('id', '')}")
    print()

    employees = await sheets.read_sheet("employees")
    if not employees:
        print("❌ Лист employees пуст.\n")
        return 1

    updated = 0
    unmatched_employees: list[str] = []
    used_lists: set[str] = set()

    print("Сопоставление с employees:\n")
    for offset, row in enumerate(employees):
        name = str(row.get("name", "")).strip()
        if not name:
            continue

        match = _match_tasklist(name, tasklists)
        row_index = offset + 2
        current = str(row.get("google_tasks_id", "")).strip()

        if match is None:
            unmatched_employees.append(name)
            print(f"  ⚠️  {name!r} — список не найден")
            continue

        list_id = str(match.get("id", "")).strip()
        list_title = str(match.get("title", "")).strip()
        used_lists.add(list_id)

        if current == list_id:
            print(f"  ✓  {name!r} → «{list_title}» (уже привязан)")
            continue

        print(f"  →  {name!r} → «{list_title}» ({list_id})")
        if apply:
            merged = dict(row)
            merged["google_tasks_id"] = list_id
            await sheets.update_row("employees", row_index, merged)
            updated += 1

    unused = [
        tl
        for tl in tasklists
        if str(tl.get("id", "")).strip() not in used_lists
        and _normalize_name(str(tl.get("title", ""))) not in _SKIP_LIST_TITLES
    ]
    manager_name = (settings.google_tasks_manager_name or "").strip()
    my_title = (settings.google_tasks_my_list_title or "Мои задачи").strip()
    my_list = next(
        (
            tl
            for tl in tasklists
            if _normalize_name(str(tl.get("title", ""))) == _normalize_name(my_title)
        ),
        None,
    )
    if manager_name and my_list:
        list_id = str(my_list.get("id", "")).strip()
        list_title = str(my_list.get("title", "")).strip()
        for offset, row in enumerate(employees):
            if not is_manager_employee_row(row):
                continue
            name = str(row.get("name", "")).strip()
            current = str(row.get("google_tasks_id", "")).strip()
            if current == list_id:
                print(f"\n  ✓  руководитель {name!r} → «{list_title}» (уже привязан)")
                break
            print(f"\n  →  руководитель {name!r} → «{list_title}» ({list_id})")
            if apply:
                merged = dict(row)
                merged["google_tasks_id"] = list_id
                await sheets.update_row("employees", offset + 2, merged)
                updated += 1
            break

    if unused:
        print("\nСписки Tasks без сотрудника в таблице:")
        for tl in unused:
            print(f"  • {tl.get('title', '?')}")

    if unmatched_employees:
        print("\nДобавьте сотрудников или переименуйте списки Tasks так, чтобы имена совпадали.")
        print("Пример: сотрудник «Ира» ↔ список «Ира».")

    if apply:
        print(f"\n✅ Обновлено строк employees: {updated}")
    else:
        print("\n(dry-run) Для записи в таблицу запустите с --apply")

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
