"""Tests for employee role resolution."""

from __future__ import annotations

from src.utils.employee_role_resolve import (
    extract_usernames_from_text,
    resolve_employee_reference,
    role_to_canonical,
)


def _rows():
    return [
        {
            "name": "Гири",
            "role": "су-шеф",
            "username": "girl5719",
            "telegram_user_id": "123",
            "active": "true",
        },
        {
            "name": "Пракаш",
            "role": "шеф",
            "username": "",
            "telegram_user_id": "456",
            "active": "true",
        },
        {"name": "Ира", "role": "", "username": "", "active": "true"},
    ]


def test_role_canonical_russian():
    assert role_to_canonical("су-шеф") == "sous_chef"
    assert role_to_canonical("сушеф") == "sous_chef"
    assert role_to_canonical("шеф") == "chef"
    assert role_to_canonical("Chef") == "chef"


def test_resolve_by_role():
    r = resolve_employee_reference("су-шеф", _rows())
    assert r.ok
    assert r.canonical_name == "Гири"
    assert r.matched_by == "role"


def test_resolve_by_role_chef():
    r = resolve_employee_reference("шеф", _rows())
    assert r.ok
    assert r.canonical_name == "Пракаш"


def test_resolve_by_username():
    r = resolve_employee_reference("@girl5719", _rows())
    assert r.ok
    assert r.canonical_name == "Гири"
    assert r.matched_by == "username"


def test_resolve_by_username_without_at():
    r = resolve_employee_reference("Girl5719", _rows())
    assert r.ok
    assert r.canonical_name == "Гири"


def test_resolve_by_name():
    r = resolve_employee_reference("Гири", _rows())
    assert r.ok
    assert r.matched_by == "name"


def test_resolve_embedded_username_in_phrase():
    rows = _rows() + [
        {
            "name": "Асим",
            "role": "менеджер",
            "username": "asimhayatkhan",
            "telegram_user_id": "789",
            "active": "true",
        },
    ]
    assert extract_usernames_from_text("Асим менеджер @asimhayatkhan") == ["asimhayatkhan"]
    r = resolve_employee_reference("Асим менеджер @asimhayatkhan", rows)
    assert r.ok
    assert r.canonical_name == "Асим"
    assert r.matched_by == "username"
