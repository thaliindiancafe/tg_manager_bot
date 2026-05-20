"""Google Drive access via service account (async wrappers)."""

from __future__ import annotations

import asyncio
import io
import logging
from pathlib import Path
from typing import Any

from google.oauth2 import service_account
from googleapiclient.discovery import Resource, build
from googleapiclient.http import MediaIoBaseDownload

from src.config import settings

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]

GOOGLE_DOC_MIME = "application/vnd.google-apps.document"
GOOGLE_APPS_EXPORT_MIME: dict[str, str] = {
    GOOGLE_DOC_MIME: "text/plain",
    "application/vnd.google-apps.spreadsheet": "text/csv",
    "application/vnd.google-apps.presentation": "text/plain",
}

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_service: Resource | None = None


def _resolve_credentials_path() -> Path:
    path = Path(settings.google_credentials_json)
    if not path.is_absolute():
        path = _PROJECT_ROOT / path
    if not path.is_file():
        raise FileNotFoundError(f"Service account file not found: {path}")
    return path


def _build_service() -> Resource:
    global _service
    if _service is not None:
        return _service

    credentials = service_account.Credentials.from_service_account_file(
        str(_resolve_credentials_path()),
        scopes=SCOPES,
    )
    _service = build("drive", "v3", credentials=credentials, cache_discovery=False)
    return _service


def _decode_content(content: bytes | str) -> str:
    if isinstance(content, str):
        return content
    return content.decode("utf-8")


def _file_to_dict(file_meta: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": file_meta.get("id", ""),
        "name": file_meta.get("name", ""),
        "mime_type": file_meta.get("mimeType", ""),
        "created_time": file_meta.get("createdTime", ""),
        "modified_time": file_meta.get("modifiedTime", ""),
        "size": file_meta.get("size", ""),
        "web_view_link": file_meta.get("webViewLink", ""),
    }


def _get_file_metadata_sync(file_id: str) -> dict[str, Any]:
    service = _build_service()
    return (
        service.files()
        .get(
            fileId=file_id,
            fields="id, name, mimeType, createdTime, modifiedTime, size, webViewLink",
            supportsAllDrives=True,
        )
        .execute()
    )


def _read_document_sync(file_id: str) -> str:
    meta = _get_file_metadata_sync(file_id)
    mime_type = meta.get("mimeType", "")

    if mime_type != GOOGLE_DOC_MIME:
        raise ValueError(
            f"File {file_id!r} is not a Google Doc (mimeType={mime_type!r}). "
            f"Use download_file() for other types."
        )

    service = _build_service()
    content = (
        service.files()
        .export(fileId=file_id, mimeType="text/plain")
        .execute()
    )
    return _decode_content(content)


def _list_folder_sync(folder_id: str) -> list[dict[str, Any]]:
    service = _build_service()
    query = f"'{folder_id}' in parents and trashed = false"
    files: list[dict[str, Any]] = []
    page_token: str | None = None

    while True:
        result = (
            service.files()
            .list(
                q=query,
                spaces="drive",
                fields="nextPageToken, files(id, name, mimeType, createdTime, modifiedTime, size, webViewLink)",
                pageToken=page_token,
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
            )
            .execute()
        )
        files.extend(_file_to_dict(item) for item in result.get("files", []))
        page_token = result.get("nextPageToken")
        if not page_token:
            break

    return files


def _download_file_sync(file_id: str) -> bytes:
    meta = _get_file_metadata_sync(file_id)
    mime_type = meta.get("mimeType", "")
    service = _build_service()

    if mime_type in GOOGLE_APPS_EXPORT_MIME:
        export_mime = GOOGLE_APPS_EXPORT_MIME[mime_type]
        content = (
            service.files()
            .export(fileId=file_id, mimeType=export_mime)
            .execute()
        )
        if isinstance(content, bytes):
            return content
        return content.encode("utf-8")

    if mime_type.startswith("application/vnd.google-apps."):
        raise ValueError(f"Unsupported Google Drive file type for download: {mime_type}")

    buffer = io.BytesIO()
    request = service.files().get_media(fileId=file_id)
    downloader = MediaIoBaseDownload(buffer, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()

    return buffer.getvalue()


def _check_new_files_sync(
    folder_id: str,
    known_file_ids: list[str],
) -> list[dict[str, Any]]:
    known = set(known_file_ids)
    return [item for item in _list_folder_sync(folder_id) if item.get("id") not in known]


async def read_document(file_id: str) -> str:
    """Export a Google Doc as plain text."""
    try:
        return await asyncio.to_thread(_read_document_sync, file_id)
    except Exception as exc:
        logger.error(
            "read_document failed: file_id=%s error=%s",
            file_id,
            exc,
            exc_info=True,
        )
        raise


async def list_folder(folder_id: str) -> list[dict[str, Any]]:
    """List non-trashed files in a Drive folder."""
    try:
        return await asyncio.to_thread(_list_folder_sync, folder_id)
    except Exception as exc:
        logger.error(
            "list_folder failed: folder_id=%s error=%s",
            folder_id,
            exc,
            exc_info=True,
        )
        raise


async def download_file(file_id: str) -> bytes:
    """Download file bytes (exports Google Workspace files when needed)."""
    try:
        return await asyncio.to_thread(_download_file_sync, file_id)
    except Exception as exc:
        logger.error(
            "download_file failed: file_id=%s error=%s",
            file_id,
            exc,
            exc_info=True,
        )
        raise


async def check_new_files(
    folder_id: str,
    known_file_ids: list[str],
) -> list[dict[str, Any]]:
    """Return folder files whose id is not in known_file_ids."""
    try:
        return await asyncio.to_thread(_check_new_files_sync, folder_id, known_file_ids)
    except Exception as exc:
        logger.error(
            "check_new_files failed: folder_id=%s known_count=%s error=%s",
            folder_id,
            len(known_file_ids),
            exc,
            exc_info=True,
        )
        raise
