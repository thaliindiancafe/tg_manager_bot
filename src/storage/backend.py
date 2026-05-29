"""Storage backend helpers."""

from __future__ import annotations

from src.config import settings


def is_db_backend() -> bool:
    return (getattr(settings, "storage_backend", "sheets") or "sheets").strip().lower() == "db"
