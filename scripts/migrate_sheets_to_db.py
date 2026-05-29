"""One-off migration: Sheets (SPREADSHEET_ID) -> DB (Supabase/Postgres).

Run:
  set STORAGE_BACKEND=db
  set DATABASE_URL=...
  python scripts/migrate_sheets_to_db.py

This script is idempotent: it UPSERTs into DB tables.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

# Allow `python scripts/migrate_sheets_to_db.py` from repo root on Windows.
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.db.schema import ensure_schema
from src.google import sheets
from src.storage import get_store

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("migrate_sheets_to_db")


async def _migrate_employees() -> int:
    rows = await sheets.read_sheet("employees")
    store = get_store()
    ok = 0
    for r in rows:
        name = str(r.get("name", "")).strip()
        if not name:
            continue
        await store.employees.upsert_employee(
            name=name,
            telegram_user_id=str(r.get("telegram_user_id", "")).strip(),
            username=str(r.get("username", "")).strip(),
            role=str(r.get("role", "")).strip(),
            google_tasks_id=str(r.get("google_tasks_id", "")).strip(),
            active=str(r.get("active", "true")).strip(),
        )
        ok += 1
    return ok


async def _migrate_tasks() -> int:
    rows = await sheets.read_sheet("tasks")
    store = get_store()
    ok = 0
    for r in rows:
        tid = str(r.get("task_id", "")).strip()
        title = str(r.get("title", "")).strip()
        if not tid or not title:
            continue
        await store.tasks.upsert_task(r)
        ok += 1
    return ok


async def _migrate_schedule() -> int:
    rows = await sheets.read_sheet("schedule")
    store = get_store()
    await store.schedule.replace_schedule_rows(rows)
    return len(rows)


async def _migrate_memory() -> tuple[int, int]:
    facts = await sheets.read_sheet("memory_facts")
    hist = await sheets.read_sheet("memory_history")
    store = get_store()
    facts_ok = 0
    for r in facts:
        emp = str(r.get("employee", "")).strip()
        fact = str(r.get("fact", "")).strip()
        if not emp:
            continue
        await store.memory.upsert_fact(emp, fact)
        facts_ok += 1
    hist_ok = 0
    for r in hist:
        chat_id = str(r.get("chat_id", "")).strip()
        if not chat_id:
            continue
        await store.memory.append_history(
            int(chat_id),
            str(r.get("role", "")).strip(),
            str(r.get("content", "")).strip(),
            str(r.get("timestamp", "")).strip(),
        )
        hist_ok += 1
    return facts_ok, hist_ok


async def _migrate_knowledge() -> tuple[int, int]:
    sources = await sheets.read_sheet("knowledge_sources")
    chunks = await sheets.read_sheet("knowledge_chunks")
    store = get_store()
    ok_sources = 0
    by_source: dict[str, list[dict]] = {}
    for ch in chunks:
        sid = str(ch.get("source_id", "")).strip()
        if not sid:
            continue
        by_source.setdefault(sid, []).append(
            {
                "chunk_index": int(str(ch.get("chunk_index", "0")).strip() or "0"),
                "text": str(ch.get("text", "")).strip(),
            }
        )
    for s in sources:
        sid = str(s.get("source_id", "")).strip()
        if not sid:
            continue
        await store.knowledge.upsert_source(s)
        await store.knowledge.replace_chunks_for_source(sid, by_source.get(sid, []))
        ok_sources += 1
    ok_chunks = sum(len(v) for v in by_source.values())
    return ok_sources, ok_chunks


async def _migrate_automations_chats() -> tuple[int, int]:
    autos = await sheets.read_sheet("automations")
    chats = await sheets.read_sheet("chats")
    store = get_store()
    ok_a = 0
    for a in autos:
        if not str(a.get("id", "")).strip():
            continue
        await store.automations.upsert_automation(a)
        ok_a += 1
    ok_c = 0
    for c in chats:
        if not str(c.get("chat_id", "")).strip():
            continue
        await store.chats.upsert_chat(c)
        ok_c += 1
    return ok_a, ok_c


async def main() -> None:
    await ensure_schema()
    store = get_store()
    init = getattr(store, "init", None)
    if callable(init):
        await init()

    emp = await _migrate_employees()
    tasks = await _migrate_tasks()
    sched = await _migrate_schedule()
    facts_ok, hist_ok = await _migrate_memory()
    src_ok, chunks_ok = await _migrate_knowledge()
    autos_ok, chats_ok = await _migrate_automations_chats()

    logger.info("Migrated: employees=%s tasks=%s schedule=%s", emp, tasks, sched)
    logger.info("Migrated: memory_facts=%s memory_history=%s", facts_ok, hist_ok)
    logger.info("Migrated: knowledge_sources=%s knowledge_chunks=%s", src_ok, chunks_ok)
    logger.info("Migrated: automations=%s chats=%s", autos_ok, chats_ok)


if __name__ == "__main__":
    asyncio.run(main())

