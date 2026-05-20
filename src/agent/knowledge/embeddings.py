"""Gemini text embeddings for knowledge chunks."""

from __future__ import annotations

import json
import logging
import math
from pathlib import Path

from google import genai

from src.config import settings

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_EMBED_DIR = _PROJECT_ROOT / "data" / "knowledge_embeddings"
_client: genai.Client | None = None


def _get_client() -> genai.Client:
    global _client
    if _client is None:
        _client = genai.Client(api_key=settings.gemini_api_key)
    return _client


def _embed_dir() -> Path:
    _EMBED_DIR.mkdir(parents=True, exist_ok=True)
    return _EMBED_DIR


def embedding_path(source_id: str) -> Path:
    return _embed_dir() / f"{source_id}.json"


async def embed_texts(texts: list[str]) -> list[list[float]]:
    """Batch embed via Gemini; returns one vector per text."""
    if not texts:
        return []
    try:
        client = _get_client()
        model = settings.knowledge_embedding_model
        response = await client.aio.models.embed_content(
            model=model,
            contents=texts,
        )
        vectors: list[list[float]] = []
        for emb in response.embeddings or []:
            values = getattr(emb, "values", None) or []
            vectors.append(list(values))
        if len(vectors) != len(texts):
            logger.warning(
                "embed_texts: expected %s vectors, got %s",
                len(texts),
                len(vectors),
            )
        return vectors
    except Exception as exc:
        logger.error("embed_texts failed: count=%s error=%s", len(texts), exc, exc_info=True)
        raise


def save_embeddings(source_id: str, vectors: list[list[float]]) -> None:
    path = embedding_path(source_id)
    path.write_text(json.dumps(vectors, ensure_ascii=False), encoding="utf-8")


def load_embeddings(source_id: str) -> list[list[float]]:
    path = embedding_path(source_id)
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return data
    except json.JSONDecodeError:
        logger.warning("load_embeddings: corrupt file %s", path)
    return []


def delete_embeddings(source_id: str) -> None:
    path = embedding_path(source_id)
    if path.is_file():
        path.unlink()


def cosine_similarity(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)
