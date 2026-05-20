"""Split document text into chunks for embedding."""

from __future__ import annotations


def chunk_text(text: str, max_chars: int = 900) -> list[str]:
    """Split by paragraphs, then merge/split to stay under max_chars."""
    raw = (text or "").strip()
    if not raw:
        return []

    paragraphs = [p.strip() for p in raw.replace("\r\n", "\n").split("\n\n") if p.strip()]
    if not paragraphs:
        paragraphs = [raw]

    chunks: list[str] = []
    buf = ""

    for para in paragraphs:
        if len(para) > max_chars:
            if buf:
                chunks.append(buf.strip())
                buf = ""
            start = 0
            while start < len(para):
                chunks.append(para[start : start + max_chars].strip())
                start += max_chars
            continue

        candidate = f"{buf}\n\n{para}".strip() if buf else para
        if len(candidate) <= max_chars:
            buf = candidate
        else:
            if buf:
                chunks.append(buf.strip())
            buf = para

    if buf:
        chunks.append(buf.strip())

    return [c for c in chunks if c]
