"""Tests for Google Tasks lists ↔ employees sync."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

from src.google.tasklist_employees_sync import (
    _employee_matches_list_title,
    _list_id_in_employees,
    sync_tasklists_to_employees_sheet,
)


def test_list_id_in_employees() -> None:
    rows = [{"google_tasks_id": "abc"}, {"google_tasks_id": ""}]
    assert _list_id_in_employees(rows, "abc") is True
    assert _list_id_in_employees(rows, "xyz") is False


def test_employee_matches_list_title() -> None:
    rows = [{"name": "Роксана"}]
    assert _employee_matches_list_title(rows, "Роксана") is True
    assert _employee_matches_list_title(rows, "Ирина") is False


def test_sync_skips_when_already_linked() -> None:
    tasklists = [{"id": "list-1", "title": "Ира"}]
    employees = [{"name": "Ира", "google_tasks_id": "list-1", "active": "true"}]

    with (
        patch("src.google.tasklist_employees_sync.oauth_configured", return_value=True),
        patch("src.google.tasklist_employees_sync.settings") as mock_settings,
        patch(
            "src.google.tasklist_employees_sync.google_tasks.list_tasklists",
            new=AsyncMock(return_value=tasklists),
        ),
        patch(
            "src.google.tasklist_employees_sync.sheets.read_sheet",
            new=AsyncMock(return_value=employees),
        ),
        patch(
            "src.google.tasklist_employees_sync.sheets.update_row",
            new=AsyncMock(),
        ) as update_row,
        patch(
            "src.google.tasklist_employees_sync.sheets.append_row",
            new=AsyncMock(),
        ) as append_row,
    ):
        mock_settings.google_tasks_use_oauth = True
        mock_settings.google_tasks_manager_name = ""
        mock_settings.google_tasks_my_list_title = "Мои задачи"

        result = asyncio.run(sync_tasklists_to_employees_sheet(apply=True))

    assert result["ok"] is True
    assert result["updated"] == 0
    assert result["created"] == 0
    update_row.assert_not_called()
    append_row.assert_not_called()


def test_sync_auto_registers_new_list_without_duplicate() -> None:
    tasklists = [
        {"id": "list-new", "title": "Сара"},
        {"id": "list-old", "title": "Ира"},
    ]
    employees = [{"name": "Ира", "google_tasks_id": "list-old", "active": "true"}]

    with (
        patch("src.google.tasklist_employees_sync.oauth_configured", return_value=True),
        patch("src.google.tasklist_employees_sync.settings") as mock_settings,
        patch(
            "src.google.tasklist_employees_sync.google_tasks.list_tasklists",
            new=AsyncMock(return_value=tasklists),
        ),
        patch(
            "src.google.tasklist_employees_sync.sheets.read_sheet",
            new=AsyncMock(return_value=employees),
        ),
        patch(
            "src.google.tasklist_employees_sync.sheets.update_row",
            new=AsyncMock(),
        ),
        patch(
            "src.google.tasklist_employees_sync.sheets.append_row",
            new=AsyncMock(),
        ) as append_row,
    ):
        mock_settings.google_tasks_use_oauth = True
        mock_settings.google_tasks_manager_name = ""
        mock_settings.google_tasks_my_list_title = "Мои задачи"

        result = asyncio.run(
            sync_tasklists_to_employees_sheet(
                apply=True,
                auto_register_from_lists=True,
            )
        )

    assert result["ok"] is True
    assert result["created"] == 1
    append_row.assert_called_once()
    call_row = append_row.call_args[0][1]
    assert call_row["name"] == "Сара"
    assert call_row["google_tasks_id"] == "list-new"
