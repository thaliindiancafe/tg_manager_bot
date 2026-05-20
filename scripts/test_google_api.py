"""Test Google API connectivity via service account."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from google.oauth2 import service_account
from googleapiclient.discovery import build

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.config import settings  # noqa: E402

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/tasks",
    "https://www.googleapis.com/auth/drive.readonly",
]

_PROJECT_ROOT = _ROOT


def _credentials_path() -> Path:
    path = Path(settings.google_credentials_json)
    if not path.is_absolute():
        path = _PROJECT_ROOT / path
    if not path.is_file():
        raise FileNotFoundError(f"Service account file not found: {path}")
    return path


def _credentials():
    return service_account.Credentials.from_service_account_file(
        str(_credentials_path()),
        scopes=SCOPES,
    )


def _ok(service: str, detail: str = "") -> None:
    suffix = f" ({detail})" if detail else ""
    print(f"✅ {service} подключён{suffix}")


def _fail(service: str, error: Exception) -> None:
    print(f"❌ {service} ошибка: {error}")


def test_sheets() -> None:
    try:
        service = build("sheets", "v4", credentials=_credentials(), cache_discovery=False)
        meta = (
            service.spreadsheets()
            .get(
                spreadsheetId=settings.spreadsheet_id,
                fields="sheets.properties.title",
            )
            .execute()
        )
        titles = [
            sheet["properties"]["title"]
            for sheet in meta.get("sheets", [])
            if sheet.get("properties", {}).get("title")
        ]
        _ok("Sheets", f"листы: {', '.join(titles) if titles else 'нет вкладок'}")
    except Exception as exc:
        _fail("Sheets", exc)


def test_calendar() -> None:
    try:
        service = build("calendar", "v3", credentials=_credentials(), cache_discovery=False)
        result = service.calendarList().list().execute()
        items = result.get("items", [])
        names = [item.get("summary", item.get("id", "?")) for item in items[:5]]
        extra = f", всего {len(items)}" if len(items) > 5 else ""
        detail = ", ".join(names) + extra if names else "календарей нет"
        _ok("Calendar", detail)
    except Exception as exc:
        _fail("Calendar", exc)


def test_tasks() -> None:
    try:
        service = build("tasks", "v1", credentials=_credentials(), cache_discovery=False)
        result = service.tasklists().list().execute()
        items = result.get("items", [])
        names = [item.get("title", item.get("id", "?")) for item in items[:5]]
        detail = ", ".join(names) if names else "списков задач нет"
        _ok("Tasks", detail)
    except Exception as exc:
        _fail("Tasks", exc)


def test_drive() -> None:
    folder_id = os.getenv("DRIVE_KNOWLEDGE_FOLDER_ID", "").strip()
    if not folder_id:
        _fail("Drive", ValueError("DRIVE_KNOWLEDGE_FOLDER_ID не задан в .env"))
        return

    try:
        service = build("drive", "v3", credentials=_credentials(), cache_discovery=False)
        folder = (
            service.files()
            .get(
                fileId=folder_id,
                fields="id, name, mimeType",
                supportsAllDrives=True,
            )
            .execute()
        )
        name = folder.get("name", folder_id)
        _ok("Drive", f"папка: {name}")
    except Exception as exc:
        _fail("Drive", exc)


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    print("Проверка Google API (service account)\n")
    test_sheets()
    test_calendar()
    test_tasks()
    test_drive()
    print()


if __name__ == "__main__":
    main()
