"""Read/write helpers that route to DB or Sheets by STORAGE_BACKEND."""

from __future__ import annotations

from typing import Any

from src.storage.backend import is_db_backend


async def list_employees() -> list[dict[str, Any]]:
    if is_db_backend():
        from src.storage import get_store

        return await get_store().employees.list_employees()
    from src.google import sheets

    return await sheets.read_sheet("employees")


async def list_automations() -> list[dict[str, Any]]:
    if is_db_backend():
        from src.storage import get_store

        return await get_store().automations.list_automations()
    from src.google import sheets

    return await sheets.read_sheet("automations")


async def list_chats() -> list[dict[str, Any]]:
    if is_db_backend():
        from src.storage import get_store

        return await get_store().chats.list_chats()
    from src.google import sheets

    return await sheets.read_sheet("chats")


async def list_knowledge_sources() -> list[dict[str, Any]]:
    if is_db_backend():
        from src.storage import get_store

        return await get_store().knowledge.list_sources()
    from src.google import sheets

    return await sheets.read_sheet("knowledge_sources")


async def list_knowledge_chunks() -> list[dict[str, Any]]:
    if is_db_backend():
        from src.storage import get_store

        return await get_store().knowledge.list_chunks()
    from src.google import sheets

    return await sheets.read_sheet("knowledge_chunks")


async def upsert_knowledge_source_row(
    source_id: str,
    row: dict[str, Any],
    existing: dict[str, Any] | None = None,
) -> None:
    if is_db_backend():
        from src.storage import get_store

        await get_store().knowledge.upsert_source(row)
        return
    from src.google import sheets

    await sheets.upsert_knowledge_source_row(source_id, row, existing)


async def replace_knowledge_chunks(
    source_id: str,
    chunks: list[dict[str, Any]],
) -> None:
    """Replace all chunks for source_id. Each item: chunk_index (int), text (str)."""
    if is_db_backend():
        from src.storage import get_store

        await get_store().knowledge.replace_chunks_for_source(source_id, chunks)
        return

    from src.google import sheets

    rows = await sheets.read_sheet("knowledge_chunks")
    sid = str(source_id).strip()
    to_delete = [
        offset + 2
        for offset, row in enumerate(rows)
        if str(row.get("source_id", "")).strip() == sid
    ]
    for row_index in sorted(to_delete, reverse=True):
        await sheets.delete_row("knowledge_chunks", row_index)
    for ch in chunks:
        await sheets.append_row(
            "knowledge_chunks",
            {
                "source_id": sid,
                "chunk_index": str(ch.get("chunk_index", 0)),
                "text": str(ch.get("text", ""))[:8000],
            },
        )


async def link_employee_google_tasks_id(
    row: dict[str, Any],
    list_id: str,
    *,
    sheet_row_index: int | None = None,
) -> None:
    """Update employees.google_tasks_id for an existing row."""
    merged = dict(row)
    merged["google_tasks_id"] = list_id
    if is_db_backend():
        from src.storage import get_store

        await get_store().employees.upsert_employee(
            name=str(merged.get("name", "")).strip(),
            telegram_user_id=str(merged.get("telegram_user_id", "")).strip(),
            username=str(merged.get("username", "")).strip(),
            role=str(merged.get("role", "")).strip(),
            google_tasks_id=list_id,
            active=str(merged.get("active", "true")).strip(),
        )
        return
    if sheet_row_index is None:
        raise ValueError("sheet_row_index required when STORAGE_BACKEND=sheets")
    from src.google import sheets

    await sheets.update_row("employees", sheet_row_index, merged)


async def upsert_employee_row(
    *,
    name: str,
    telegram_user_id: str = "",
    username: str = "",
    role: str = "",
    google_tasks_id: str = "",
    active: str = "true",
) -> None:
    if is_db_backend():
        from src.storage import get_store

        await get_store().employees.upsert_employee(
            name=name,
            telegram_user_id=telegram_user_id,
            username=username,
            role=role,
            google_tasks_id=google_tasks_id,
            active=active,
        )
        return
    from src.google import sheets

    await sheets.append_row(
        "employees",
        {
            "name": name,
            "telegram_user_id": telegram_user_id,
            "username": username,
            "role": role,
            "google_tasks_id": google_tasks_id,
            "active": active,
        },
    )
