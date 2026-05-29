"""Database engine/pool setup."""

from __future__ import annotations

import logging
import re
import ssl
from functools import lru_cache
from urllib.parse import urlparse

from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from src.config import settings

logger = logging.getLogger(__name__)


def _normalize_db_url(url: str) -> str:
    value = (url or "").strip()
    if not value:
        return ""
    # Allow providing sync URL (postgresql://...) and convert to asyncpg dialect
    if value.startswith("postgresql://") and "+asyncpg" not in value:
        value = value.replace("postgresql://", "postgresql+asyncpg://", 1)

    # Supabase UI shows [YOUR-PASSWORD] — brackets break URL parsing (treated as IPv6).
    if re.search(r":\[[^\]]+\]@", value):
        logger.warning(
            "DATABASE_URL: remove square brackets around password "
            "(use postgres:password@host, not postgres:[password]@host)"
        )
        value = re.sub(r":\[([^\]]+)\]@", r":\1@", value)

    return value


def _connect_args_for_url(url: str) -> dict:
    """Supabase requires TLS. On some Windows PCs antivirus breaks cert verify (DEV workaround)."""
    host = (urlparse(url).hostname or "").lower()
    if not (host.endswith("supabase.co") or host.endswith("pooler.supabase.com")):
        return {}

    ctx = ssl.create_default_context()
    if not bool(getattr(settings, "database_ssl_verify", True)):
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        logger.warning(
            "DATABASE_SSL_VERIFY=false: TLS certificate verification disabled for DB"
        )
    # Supabase transaction pooler (PgBouncer) does not support asyncpg prepared statements.
    return {"ssl": ctx, "statement_cache_size": 0}


@lru_cache(maxsize=1)
def get_engine() -> AsyncEngine:
    url = _normalize_db_url(getattr(settings, "database_url", ""))
    if not url:
        raise RuntimeError("DATABASE_URL is required when STORAGE_BACKEND=db")

    host = (urlparse(url).hostname or "").lower()
    if host.startswith("db.") and host.endswith(".supabase.co"):
        logger.warning(
            "DATABASE_URL uses Supabase Direct (db.*.supabase.co). "
            "On IPv4-only networks use Transaction pooler (port 6543) from dashboard Connect."
        )

    connect_args = _connect_args_for_url(url)
    engine = create_async_engine(
        url,
        connect_args=connect_args,
        pool_pre_ping=True,
        pool_size=int(getattr(settings, "db_pool_size", 5) or 5),
        max_overflow=int(getattr(settings, "db_max_overflow", 5) or 5),
    )
    logger.info("DB engine initialized")
    return engine

