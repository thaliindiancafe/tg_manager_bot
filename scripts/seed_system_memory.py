"""Write system memory_facts rows (product capabilities for the agent)."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.agent.system_memory import SYSTEM_MEMORY_FACTS, seed_system_memory_facts


def _configure_stdout() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")


async def main() -> None:
    _configure_stdout()
    print("Запись служебных фактов в memory_facts...\n")
    results = await seed_system_memory_facts()
    for key in sorted(SYSTEM_MEMORY_FACTS):
        status = results.get(key, "missing")
        mark = "✅" if status == "ok" else "❌"
        print(f"{mark} {key}: {status}")
    print()


if __name__ == "__main__":
    asyncio.run(main())
