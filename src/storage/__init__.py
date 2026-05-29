"""Storage backend factory."""

from __future__ import annotations

from functools import lru_cache

from src.config import settings
from src.storage.base import Store


@lru_cache(maxsize=1)
def get_store() -> Store:
    backend = (getattr(settings, "storage_backend", "sheets") or "sheets").strip().lower()
    if backend == "db":
        from src.storage.db_store import DBStore

        return DBStore()

    from src.storage.sheets_store import SheetsStore

    return SheetsStore()


def reset_store_cache() -> None:
    get_store.cache_clear()

