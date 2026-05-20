"""Internal smoke checks before client acceptance (phases 1-4). No Telegram calls."""

from __future__ import annotations

import asyncio
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _ok(msg: str) -> None:
    print(f"  OK  {msg}")


def _fail(msg: str) -> None:
    print(f"  FAIL  {msg}")
    raise SystemExit(1)


def check_compile() -> None:
    print("1. compileall")
    r = subprocess.run(
        [sys.executable, "-m", "compileall", "-q", "src", "main.py", "scripts"],
        cwd=_ROOT,
        capture_output=True,
        text=True,
    )
    if r.returncode != 0:
        _fail(f"compileall: {r.stderr or r.stdout}")
    _ok("Python sources compile")


def check_tool_registry() -> None:
    print("2. Gemini tool registry")
    from src.agent.client import TOOL_REGISTRY

    required = {
        "delegate_private_reminder",
        "get_open_tasks_for_telegram_user",
        "assert_task_for_telegram_user",
        "complete_task",
        "send_brief_to_primary_work_chat",
        "submit_task_proof",
        "approve_task",
        "reject_task_proof",
    }
    missing = required - set(TOOL_REGISTRY)
    if missing:
        _fail(f"missing tools: {sorted(missing)}")
    _ok(f"{len(required)} delegation tools registered")


def check_handlers() -> None:
    print("3. Bot handlers")
    from src.bot.handlers import chatid_router, start_router
    from src.bot.handlers.delegation_callbacks import router as cb_router
    from src.bot.handlers.delegation_routing import resolve_proof_task_id

    assert start_router.name == "start"
    assert chatid_router.name == "chatid"
    assert cb_router.name == "delegation_callbacks"
    assert callable(resolve_proof_task_id)
    _ok("handlers import (start, chatid, delegation)")


def check_scheduler() -> None:
    print("4. Scheduler jobs")
    import inspect

    from src.scheduler import automations, runner

    assert "run_automations" in inspect.getsource(runner.start_scheduler)
    assert "sync_schedule" in inspect.getsource(runner.start_scheduler)
    assert callable(automations.run_task_due_reminders)
    _ok("run_automations + sync_schedule + task_reminders")


async def check_env_optional() -> None:
    print("5. Environment (optional)")
    try:
        from src.config import settings

        _ok(f"SPREADSHEET_ID set ({len(settings.spreadsheet_id)} chars)")
        _ok(f"TIMEZONE={settings.timezone}")
    except Exception as exc:
        print(f"  WARN  config: {exc} (run with .env for full check)")


def main() -> None:
    print("Smoke acceptance (internal)\n")
    check_compile()
    check_tool_registry()
    check_handlers()
    check_scheduler()
    asyncio.run(check_env_optional())
    print("\nManual Telegram checks still required — see docs/client-acceptance-test-ru.md")


if __name__ == "__main__":
    main()
