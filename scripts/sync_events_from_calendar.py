"""One-off: sync Google Calendar → events sheet (same as daily cron)."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.scheduler.events_sync import sync_events_from_google_calendars


async def _main() -> int:
    count = await sync_events_from_google_calendars()
    print(f"OK: synced {count} event rows to sheet 'events'.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
