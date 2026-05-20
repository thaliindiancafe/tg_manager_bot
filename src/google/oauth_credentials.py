"""OAuth user credentials for Google APIs (personal Gmail, e.g. Google Tasks)."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

from src.config import settings

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

TASKS_SCOPES = ("https://www.googleapis.com/auth/tasks",)
CALENDAR_READONLY_SCOPES = ("https://www.googleapis.com/auth/calendar.readonly",)
# Tasks + Calendar read (one OAuth consent for client's Gmail)
USER_OAUTH_SCOPES = TASKS_SCOPES + CALENDAR_READONLY_SCOPES


def _resolve_path(path_str: str) -> Path:
    path = Path(path_str.strip())
    if not path.is_absolute():
        path = _PROJECT_ROOT / path
    return path


def oauth_token_path() -> Path:
    return _resolve_path(settings.google_tasks_oauth_token_path)


def oauth_client_secrets_path() -> Path:
    return _resolve_path(settings.google_tasks_oauth_client_json)


def oauth_configured() -> bool:
    token = oauth_token_path()
    client = oauth_client_secrets_path()
    return token.is_file() and client.is_file()


def oauth_token_scopes() -> set[str]:
    if not oauth_configured():
        return set()
    try:
        data = json.loads(oauth_token_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    raw = data.get("scopes") or []
    if isinstance(raw, str):
        return {raw}
    return {str(s) for s in raw}


def oauth_has_calendar_read_scope() -> bool:
    return any(
        scope in oauth_token_scopes()
        for scope in (
            "https://www.googleapis.com/auth/calendar.readonly",
            "https://www.googleapis.com/auth/calendar",
        )
    )


def load_user_credentials(
    scopes: tuple[str, ...] = USER_OAUTH_SCOPES,
) -> Credentials:
    """
    Load refreshable OAuth credentials from token file.
    Raises FileNotFoundError if setup was not run.
    """
    token_path = oauth_token_path()
    client_path = oauth_client_secrets_path()

    if not client_path.is_file():
        raise FileNotFoundError(
            f"OAuth client secrets not found: {client_path}. "
            "Create OAuth Desktop client in Google Cloud Console and set "
            "GOOGLE_TASKS_OAUTH_CLIENT_JSON in .env"
        )
    if not token_path.is_file():
        raise FileNotFoundError(
            f"OAuth token not found: {token_path}. "
            "Run: python scripts/google_tasks_oauth_setup.py"
        )

    creds = Credentials.from_authorized_user_file(str(token_path), list(scopes))
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        token_path.write_text(creds.to_json(), encoding="utf-8")
        logger.info("Refreshed OAuth token: %s", token_path.name)
    elif creds.expired:
        raise RuntimeError(
            f"OAuth token expired and no refresh_token in {token_path}. "
            "Re-run: python scripts/google_tasks_oauth_setup.py"
        )
    return creds


def run_local_oauth_consent(
    scopes: tuple[str, ...] = USER_OAUTH_SCOPES,
) -> Path:
    """Open browser for one-time consent; save token to configured path."""
    client_path = oauth_client_secrets_path()
    if not client_path.is_file():
        raise FileNotFoundError(
            f"OAuth client secrets not found: {client_path}"
        )

    flow = InstalledAppFlow.from_client_secrets_file(
        str(client_path),
        list(scopes),
    )
    creds = flow.run_local_server(port=0, prompt="consent")
    token_path = oauth_token_path()
    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(creds.to_json(), encoding="utf-8")
    logger.info("Saved OAuth token to %s", token_path)
    return token_path
