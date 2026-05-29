"""Tests for employee register fast-path gating."""

from __future__ import annotations

from src.bot.employee_register_query import _looks_like_bulk_register

CLIENT_GOOGLE_TASKS_MESSAGE = (
    "@thali_manager_bot проставь задачи в гугл таск, те которые появлялись "
    "в чате за посдение 7 дней. Исследуй диалог посотри у кого какие задачи "
    "появлялись (кто кому ставил в диалоге перереписке) и добавь эти новые "
    "задачи в гугл таск"
)

BULK_EMPLOYEES_MESSAGE = """Внеси в базу сотрудников:
Ирина, менеджер - @irina_kovaleva_l
Азиз, менеджер - @ashura_skr"""


def test_google_tasks_chat_request_not_bulk_register():
    assert _looks_like_bulk_register(CLIENT_GOOGLE_TASKS_MESSAGE) is False


def test_multiline_employee_list_is_bulk_register():
    assert _looks_like_bulk_register(BULK_EMPLOYEES_MESSAGE) is True
