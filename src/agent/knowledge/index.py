"""Index Drive folder into knowledge_sources + knowledge_chunks."""

from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from src.agent.knowledge.chunking import chunk_text
from src.agent.knowledge.embeddings import delete_embeddings, embed_texts, save_embeddings
from src.agent.knowledge.extract import extract_text_from_drive_file
from src.config import settings
from src.google import drive as google_drive
from src.google import sheets

logger = logging.getLogger(__name__)

_SYSTEM_KNOWLEDGE_FACT = "_system_knowledge"
_SKIP_MIME_PREFIXES = ("application/vnd.google-apps.folder", "image/", "video/")


def _now_str() -> str:
    return datetime.now(ZoneInfo(settings.timezone)).strftime("%Y-%m-%d %H:%M:%S")


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _find_source_by_drive_id(
    sources: list[dict[str, Any]], drive_file_id: str
) -> dict[str, Any] | None:
    for row in sources:
        if str(row.get("drive_file_id", "")).strip() == drive_file_id:
            return row
    return None


async def _delete_chunks_for_source(source_id: str) -> None:
    rows = await sheets.read_sheet("knowledge_chunks")
    to_delete = [
        offset + 2
        for offset, row in enumerate(rows)
        if str(row.get("source_id", "")).strip() == source_id
    ]
    for row_index in sorted(to_delete, reverse=True):
        await sheets.delete_row("knowledge_chunks", row_index)


async def index_drive_file(
    file_meta: dict[str, Any],
    existing_sources: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Index one Drive file; skip if content hash unchanged."""
    drive_file_id = str(file_meta.get("id", "")).strip()
    title = str(file_meta.get("name", "")).strip() or drive_file_id
    mime_type = str(file_meta.get("mime_type", "")).strip()

    if not drive_file_id:
        return {"ok": False, "error": "empty drive file id"}

    if any(mime_type.startswith(p) for p in _SKIP_MIME_PREFIXES):
        return {"ok": False, "skipped": True, "reason": "unsupported mime", "title": title}

    sources = existing_sources if existing_sources is not None else await sheets.read_sheet(
        "knowledge_sources"
    )
    existing = _find_source_by_drive_id(sources, drive_file_id)
    source_id = str(existing.get("source_id", "")).strip() if existing else str(uuid.uuid4())

    try:
        text = await extract_text_from_drive_file(drive_file_id, mime_type)
    except Exception as exc:
        err = str(exc)
        row = {
            "source_id": source_id,
            "drive_file_id": drive_file_id,
            "title": title,
            "mime_type": mime_type,
            "content_hash": "",
            "indexed_at": _now_str(),
            "active": "false",
            "chunk_count": "0",
            "error": err[:500],
        }
        await sheets.upsert_knowledge_source_row(source_id, row, existing)
        return {"ok": False, "title": title, "error": err}

    if not text.strip():
        return {"ok": False, "title": title, "error": "пустой текст"}

    digest = _content_hash(text)
    if existing and str(existing.get("content_hash", "")).strip() == digest:
        return {"ok": True, "title": title, "skipped": True, "reason": "unchanged"}

    chunks = chunk_text(text, max_chars=settings.knowledge_chunk_max_chars)
    if not chunks:
        return {"ok": False, "title": title, "error": "не удалось нарезать текст"}

    vectors = await embed_texts(chunks)
    if len(vectors) != len(chunks):
        logger.warning(
            "index_drive_file: embedding count mismatch file=%s", drive_file_id
        )

    await _delete_chunks_for_source(source_id)
    delete_embeddings(source_id)
    if vectors:
        save_embeddings(source_id, vectors)

    for idx, chunk in enumerate(chunks):
        await sheets.append_row(
            "knowledge_chunks",
            {
                "source_id": source_id,
                "chunk_index": str(idx),
                "text": chunk[:8000],
            },
        )

    source_row = {
        "source_id": source_id,
        "drive_file_id": drive_file_id,
        "title": title,
        "mime_type": mime_type,
        "content_hash": digest,
        "indexed_at": _now_str(),
        "active": "true",
        "chunk_count": str(len(chunks)),
        "error": "",
    }
    await sheets.upsert_knowledge_source_row(source_id, source_row, existing)

    return {
        "ok": True,
        "source_id": source_id,
        "title": title,
        "chunk_count": len(chunks),
        "reindexed": True,
    }


async def sync_drive_knowledge_folder(folder_id: str | None = None) -> dict[str, Any]:
    """
    List Drive folder and index new/changed files.
    Returns summary dict for logging.
    """
    fid = (folder_id or settings.drive_knowledge_folder_id or "").strip()
    if not fid:
        logger.warning("sync_drive_knowledge_folder: DRIVE_KNOWLEDGE_FOLDER_ID not set")
        return {"ok": False, "error": "DRIVE_KNOWLEDGE_FOLDER_ID not configured"}

    indexed = 0
    skipped = 0
    errors: list[str] = []

    try:
        files = await google_drive.list_folder(fid)
        sources = await sheets.read_sheet("knowledge_sources")

        for meta in files:
            mime = str(meta.get("mime_type", ""))
            if mime == "application/vnd.google-apps.folder":
                continue
            try:
                result = await index_drive_file(meta, existing_sources=sources)
                if result.get("skipped"):
                    skipped += 1
                elif result.get("ok"):
                    indexed += 1
                    sources = await sheets.read_sheet("knowledge_sources")
                else:
                    errors.append(f"{result.get('title', '?')}: {result.get('error', '?')}")
            except Exception as exc:
                logger.error(
                    "sync_drive_knowledge_folder file failed: %s",
                    meta.get("id"),
                    exc_info=True,
                )
                errors.append(f"{meta.get('name', '?')}: {exc}")

        fact = (
            f"База знаний Drive: папка проиндексирована {_now_str()}. "
            f"Новых/обновлённых: {indexed}, без изменений: {skipped}. "
            f"Для ответов используй search_knowledge."
        )
        await sheets.upsert_memory_fact_row(_SYSTEM_KNOWLEDGE_FACT, fact)

        summary = {
            "ok": True,
            "folder_id": fid,
            "files_seen": len(files),
            "indexed": indexed,
            "skipped_unchanged": skipped,
            "errors": errors[:10],
        }
        logger.info("sync_drive_knowledge_folder: %s", summary)
        return summary
    except Exception as exc:
        logger.error("sync_drive_knowledge_folder failed: %s", exc, exc_info=True)
        raise
