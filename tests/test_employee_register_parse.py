"""Tests for bulk employee line parsing."""

from __future__ import annotations

from src.utils.employee_register_parse import (
    parse_employee_line,
    parse_employees_bulk_text,
)


def test_parse_line_with_username():
    p = parse_employee_line("Ирина, менеджер - @irina_kovaleva_l")
    assert p is not None
    assert p.name == "Ирина"
    assert p.role == "менеджер"
    assert p.username == "irina_kovaleva_l"


def test_parse_numbered_line():
    p = parse_employee_line("3. Азиз, менеджер - @ashura_skr")
    assert p is not None
    assert p.name == "Азиз"
    assert p.username == "ashura_skr"


def test_client_examples():
    p = parse_employee_line("Пракаш, шеф повар - Пракаш")
    assert p is not None
    assert p.name == "Пракаш"
    assert p.role == "шеф повар"
    assert p.username == ""

    p = parse_employee_line("Макс , повар - Мах")
    assert p is not None
    assert p.name == "Макс"
    assert p.role == "повар"

    p = parse_employee_line("Гири, су- шеф- @Giri5719")
    assert p is not None
    assert p.name == "Гири"
    assert p.role == "су- шеф"
    assert p.username == "Giri5719"

    p = parse_employee_line("Мохит, уборщик- Mohit")
    assert p is not None
    assert p.name == "Мохит"
    assert p.role == "уборщик"
    assert p.username == "Mohit"


def test_parse_bulk_skips_noise():
    text = """
внеси в свою базу данных сотрудников
1. Ирина, менеджер - @irina_kovaleva_l
2. Августа, бариста - @gentlyalien
"""
    rows = parse_employees_bulk_text(text)
    assert len(rows) == 2
    assert rows[0].name == "Ирина"
    assert rows[1].username == "gentlyalien"
