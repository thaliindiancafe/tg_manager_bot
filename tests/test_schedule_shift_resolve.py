"""Tests for shift unpacking resolution from schedule."""

from __future__ import annotations

import asyncio

from src.utils.schedule_shift_resolve import (
    is_shift_unpacking_query,
    resolve_shift_unpacking_from_schedule,
)


def test_shift_unpacking_query_detection():
    assert is_shift_unpacking_query("помощник на смене")
    assert is_shift_unpacking_query("помощник по смене")
    assert is_shift_unpacking_query("разбор товара")
    assert not is_shift_unpacking_query("помощник")
    assert not is_shift_unpacking_query("Ира")


def test_resolve_from_schedule_mock():
    from unittest.mock import patch

    async def fake_read(sheet: str):
        assert sheet == "schedule"
        return [
            {
                "date": "2026-05-19",
                "employee": "Ира",
                "role": "Kleener",
                "shift_start": "10:00",
                "shift_end": "18:00",
            },
        ]

    employees = [{"name": "Ира", "active": "true"}]
    with (
        patch(
            "src.utils.schedule_shift_resolve.sheets.read_sheet",
            fake_read,
        ),
        patch(
            "src.utils.schedule_shift_resolve._today_iso",
            lambda: "2026-05-19",
        ),
    ):
        result = asyncio.run(resolve_shift_unpacking_from_schedule(employees))
    assert result.ok
    assert result.canonical_name == "Ира"
    assert result.matched_by == "schedule_unpacking"
