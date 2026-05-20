"""Semantic search over indexed knowledge chunks."""

from __future__ import annotations

import logging
from typing import Any

from src.agent.knowledge.embeddings import (
    cosine_similarity,
    embed_texts,
    load_embeddings,
)
from src.config import settings
from src.google import sheets

logger = logging.getLogger(__name__)


async def search_knowledge(query: str, top_k: int | None = None) -> dict[str, Any]:
    """
    Return top matching chunks with source titles for agent context.
    """
    q = (query or "").strip()
    if not q:
        return {"ok": False, "error": "Пустой запрос", "matches": []}

    limit = top_k if top_k is not None else settings.knowledge_search_top_k
    sources = await sheets.read_sheet("knowledge_sources")
    active_ids = {
        str(s.get("source_id", "")).strip()
        for s in sources
        if str(s.get("active", "")).strip().lower() in {"true", "1", "yes", "on"}
    }
    title_by_id = {
        str(s.get("source_id", "")).strip(): str(s.get("title", "")).strip()
        for s in sources
    }

    chunks = await sheets.read_sheet("knowledge_chunks")
    candidates: list[tuple[float, dict[str, Any]]] = []

    try:
        query_vec = (await embed_texts([q]))[0]
    except Exception as exc:
        logger.error("search_knowledge embed query failed: %s", exc, exc_info=True)
        return {"ok": False, "error": str(exc), "matches": []}

    by_source: dict[str, list[dict[str, Any]]] = {}
    for row in chunks:
        sid = str(row.get("source_id", "")).strip()
        if sid not in active_ids:
            continue
        by_source.setdefault(sid, []).append(row)

    for sid, rows in by_source.items():
        vectors = load_embeddings(sid)
        sorted_rows = sorted(rows, key=lambda r: int(str(r.get("chunk_index", "0") or "0")))
        for idx, row in enumerate(sorted_rows):
            text = str(row.get("text", "")).strip()
            if not text:
                continue
            if idx < len(vectors) and vectors[idx]:
                score = cosine_similarity(query_vec, vectors[idx])
            else:
                score = _keyword_score(q, text)
            candidates.append(
                (
                    score,
                    {
                        "source_id": sid,
                        "title": title_by_id.get(sid, ""),
                        "chunk_index": row.get("chunk_index", ""),
                        "text": text[:2000],
                        "score": round(score, 4),
                    },
                )
            )

    candidates.sort(key=lambda x: x[0], reverse=True)
    matches = [item for _, item in candidates[:limit]]

    return {
        "ok": True,
        "query": q,
        "match_count": len(matches),
        "matches": matches,
    }


def _keyword_score(query: str, text: str) -> float:
    q_tokens = {t for t in query.lower().split() if len(t) > 2}
    if not q_tokens:
        return 0.0
    lower = text.lower()
    hits = sum(1 for t in q_tokens if t in lower)
    return hits / len(q_tokens)
