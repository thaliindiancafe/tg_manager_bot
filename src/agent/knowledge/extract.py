"""Extract plain text from Drive files for indexing."""

from __future__ import annotations

import logging

from src.google import drive as google_drive

logger = logging.getLogger(__name__)

_GOOGLE_DOC = "application/vnd.google-apps.document"
_GOOGLE_SHEET = "application/vnd.google-apps.spreadsheet"
_GOOGLE_SLIDES = "application/vnd.google-apps.presentation"
_TEXT_PLAIN = "text/plain"


async def extract_text_from_drive_file(file_id: str, mime_type: str) -> str:
    """Return UTF-8 text for supported Drive mime types."""
    mime = (mime_type or "").strip()
    try:
        if mime == _GOOGLE_DOC:
            return await google_drive.read_document(file_id)

        if mime in (_GOOGLE_SHEET, _GOOGLE_SLIDES, _TEXT_PLAIN) or mime.startswith(
            "text/"
        ):
            data = await google_drive.download_file(file_id)
            return data.decode("utf-8", errors="replace")

        if mime == "application/pdf":
            pdf_bytes = await google_drive.download_file(file_id)
            return _extract_pdf_text(pdf_bytes)

        raise ValueError(
            f"Тип файла не поддерживается для индекса: {mime or 'unknown'}. "
            "Используйте Google Doc или текстовый файл."
        )
    except Exception as exc:
        logger.error(
            "extract_text_from_drive_file failed: file_id=%s mime=%s error=%s",
            file_id,
            mime,
            exc,
            exc_info=True,
        )
        raise


def _extract_pdf_text(data: bytes) -> str:
    try:
        from pypdf import PdfReader
        from io import BytesIO

        reader = PdfReader(BytesIO(data))
        parts: list[str] = []
        for page in reader.pages:
            parts.append(page.extract_text() or "")
        return "\n".join(parts).strip()
    except ImportError:
        raise ValueError(
            "PDF требует пакет pypdf. Установите: pip install pypdf"
        ) from None
