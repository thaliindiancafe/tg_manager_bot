"""Shared Google API HTTP client with explicit timeouts."""

from __future__ import annotations

import httplib2
from google.oauth2 import credentials as oauth2_credentials
from google.oauth2 import service_account
from google_auth_httplib2 import AuthorizedHttp

from src.config import settings


def build_authorized_http(
    creds: service_account.Credentials | oauth2_credentials.Credentials,
    *,
    timeout_sec: int | None = None,
) -> AuthorizedHttp:
    timeout = timeout_sec if timeout_sec is not None else settings.google_sheets_request_timeout
    base_http = httplib2.Http(timeout=timeout)
    return AuthorizedHttp(creds, http=base_http)
