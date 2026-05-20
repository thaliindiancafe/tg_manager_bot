"""Initialize Google Sheets tabs and column headers. Run from project root."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


async def main() -> None:
    force = "--force" in sys.argv
    from src.google.sheets import init_all_sheets

    print("Initializing sheets...", "(force overwrite)" if force else "")
    results = await init_all_sheets(force=force)

    for sheet_name, status in results.items():
        print(f"  {sheet_name}: {status}")

    print("Done.")


if __name__ == "__main__":
    asyncio.run(main())
