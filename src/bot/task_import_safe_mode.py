"""Safe mode for importing tasks from chat transcript into Google Tasks."""

from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import datetime
from typing import Any

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import text

from src.agent import tools as agent_tools
from src.config import settings
from src.db.engine import get_engine
logger = logging.getLogger(__name__)

_IMPORT_INTENT = re.compile(
    r"(простав(ь|ить)|добав(ь|ить)|создай|создать).{0,30}(задач|поручен).{0,30}(7|сем)[ -]?д",
    re.IGNORECASE,
)
_AT_USERNAME = re.compile(r"@([a-zA-Z][a-zA-Z0-9_]{2,})")


def looks_like_import_request(text: str) -> bool:
    return bool(_IMPORT_INTENT.search((text or "").strip()))


def _manager_name() -> str:
    return (settings.google_tasks_manager_name or "Менеджер").strip()


def _kb_confirm(draft_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Создать с исполнителем",
                    callback_data=f"task_import_confirm:{draft_id}",
                ),
                InlineKeyboardButton(
                    text="Отмена",
                    callback_data=f"task_import_cancel:{draft_id}",
                ),
            ]
        ]
    )


def _kb_unassigned(draft_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Записать в мои задачи",
                    callback_data=f"task_import_my_tasks:{draft_id}",
                ),
                InlineKeyboardButton(
                    text="Назначить через @ник",
                    callback_data=f"task_import_assign:{draft_id}",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="Пропустить без исполнителя",
                    callback_data=f"task_import_skip_unassigned:{draft_id}",
                ),
            ],
        ]
    )


async def _save_draft(*, chat_id: int, user_id: int | None, payload: dict[str, Any]) -> str:
    draft_id = str(uuid.uuid4())
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.execute(
            text(
                """
                insert into task_import_drafts(draft_id, chat_id, created_by_user_id, payload_json)
                values (:draft_id,:chat_id,:uid,:payload)
                """
            ),
            {
                "draft_id": draft_id,
                "chat_id": int(chat_id),
                "uid": int(user_id) if user_id is not None else None,
                "payload": json.dumps(payload, ensure_ascii=False),
            },
        )
    return draft_id


async def _update_draft(draft_id: str, payload: dict[str, Any]) -> None:
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.execute(
            text("update task_import_drafts set payload_json=:payload where draft_id=:id"),
            {"id": draft_id, "payload": json.dumps(payload, ensure_ascii=False)},
        )


async def _load_draft(draft_id: str) -> dict[str, Any] | None:
    engine = get_engine()
    async with engine.connect() as conn:
        res = await conn.execute(
            text("select payload_json from task_import_drafts where draft_id=:id limit 1"),
            {"id": draft_id},
        )
        row = res.first()
    if not row:
        return None
    try:
        return json.loads(str(row._mapping["payload_json"] or "{}"))
    except json.JSONDecodeError:
        return None


async def _delete_draft(draft_id: str) -> None:
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.execute(
            text("delete from task_import_drafts where draft_id=:id"),
            {"id": draft_id},
        )


async def find_draft_awaiting_username(*, chat_id: int, user_id: int) -> str | None:
    """Return draft_id if this user waits to send @username for unassigned tasks."""
    engine = get_engine()
    async with engine.connect() as conn:
        res = await conn.execute(
            text(
                """
                select draft_id, payload_json
                from task_import_drafts
                where chat_id=:chat_id and created_by_user_id=:uid
                order by created_at desc
                limit 5
                """
            ),
            {"chat_id": int(chat_id), "uid": int(user_id)},
        )
        rows = res.fetchall()
    for row in rows:
        try:
            payload = json.loads(str(row._mapping["payload_json"] or "{}"))
        except json.JSONDecodeError:
            continue
        if payload.get("phase") == "awaiting_username" and payload.get("unassigned_pending"):
            return str(row._mapping["draft_id"])
    return None


def _split_tasks(tasks: list[dict[str, Any]]) -> tuple[list[dict], list[dict]]:
    assigned: list[dict] = []
    unassigned: list[dict] = []
    for t in tasks:
        if str(t.get("assigned_to", "")).strip():
            assigned.append(t)
        else:
            unassigned.append(t)
    return assigned, unassigned


def _format_preview(tasks: list[dict[str, Any]], *, since_days: int) -> str:
    if not tasks:
        return "За последние дни не нашла явных поручений в переписке."

    assigned, unassigned = _split_tasks(tasks)
    lines = [
        f"Нашла **{len(tasks)}** возможных задач за последние **{since_days}** дней:",
        f"— с исполнителем: **{len(assigned)}**",
        f"— без исполнителя: **{len(unassigned)}**",
        "",
    ]
    shown = 0
    for t in tasks:
        if shown >= 20:
            break
        shown += 1
        title = str(t.get("title", "")).strip() or "(без названия)"
        assignee = str(t.get("assigned_to", "")).strip()
        due = str(t.get("due_date", "")).strip()
        who = f" → {assignee}" if assignee else " → ?"
        due_part = f" (до {due})" if due else ""
        lines.append(f"{shown}. {title}{who}{due_part}")

    if len(tasks) > 20:
        lines.append(f"\n…и ещё {len(tasks) - 20}.")
    lines.append("\nСначала создам задачи **с исполнителем**. Без исполнителя — спрошу отдельно.")
    return "\n".join(lines)


def format_unassigned_followup(unassigned: list[dict[str, Any]]) -> str:
    lines = [
        "У части задач **не найден исполнитель** в справочнике employees:",
        "",
    ]
    for i, t in enumerate(unassigned[:15], start=1):
        title = str(t.get("title", "")).strip()
        due = str(t.get("due_date", "")).strip()
        due_part = f" (до {due})" if due else ""
        lines.append(f"{i}. {title}{due_part}")
    if len(unassigned) > 15:
        lines.append(f"\n…и ещё {len(unassigned) - 15}.")
    lines.append(
        "\nВыберите: записать в **мои задачи** "
        f"({_manager_name()}) или назначить одного исполнителя через **@username**."
    )
    return "\n".join(lines)


async def _create_tasks_batch(tasks: list[dict[str, Any]]) -> tuple[list[dict], list[dict]]:
    created: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    for t in tasks[:200]:
        title = str(t.get("title", "")).strip()
        if not title:
            continue
        assigned_to = str(t.get("assigned_to", "")).strip()
        due_date = str(t.get("due_date", "")).strip()
        res = await agent_tools.create_task(
            title=title,
            assigned_to=assigned_to,
            due_date=due_date,
            notes=json.dumps({"evidence": t.get("evidence")}, ensure_ascii=False),
        )
        if res.get("error"):
            failed.append(
                {"title": title, "assigned_to": assigned_to, "error": res.get("error")}
            )
        else:
            created.append(res)
    return created, failed


async def start_safe_import(
    message: Message, *, since_days: int = 7
) -> tuple[str, InlineKeyboardMarkup] | None:
    if (getattr(settings, "storage_backend", "sheets") or "sheets").strip().lower() != "db":
        return (
            "Этот режим доступен только при **STORAGE_BACKEND=db** (нужна БД).",
            InlineKeyboardMarkup(inline_keyboard=[]),
        )

    data = await agent_tools.extract_tasks_from_chat(int(message.chat.id), since_days=since_days)
    if not data.get("ok"):
        return (
            str(data.get("error", "Не удалось разобрать чат.")),
            InlineKeyboardMarkup(inline_keyboard=[]),
        )

    tasks = list(data.get("tasks") or [])
    payload = {
        "since_days": int(since_days),
        "created_at": datetime.utcnow().isoformat(),
        "tasks": tasks,
        "unassigned_pending": [],
        "phase": None,
    }
    draft_id = await _save_draft(
        chat_id=int(message.chat.id),
        user_id=message.from_user.id if message.from_user else None,
        payload=payload,
    )
    return _format_preview(tasks, since_days=since_days), _kb_confirm(draft_id)


async def confirm_import(draft_id: str) -> dict[str, Any]:
    """Create tasks that already have assignee; keep unassigned in draft."""
    payload = await _load_draft(draft_id)
    if not payload:
        return {"ok": False, "error": "Черновик не найден или устарел."}

    tasks = list(payload.get("tasks") or [])
    assigned, unassigned = _split_tasks(tasks)
    created, failed = await _create_tasks_batch(assigned)

    if unassigned:
        payload["unassigned_pending"] = unassigned
        payload["phase"] = None
        await _update_draft(draft_id, payload)
        return {
            "ok": True,
            "created": created,
            "failed": failed,
            "unassigned": unassigned,
            "draft_id": draft_id,
            "needs_unassigned_action": True,
        }

    await _delete_draft(draft_id)
    return {
        "ok": True,
        "created": created,
        "failed": failed,
        "needs_unassigned_action": False,
    }


async def create_unassigned_as_my_tasks(draft_id: str) -> dict[str, Any]:
    payload = await _load_draft(draft_id)
    if not payload:
        return {"ok": False, "error": "Черновик не найден."}
    pending = list(payload.get("unassigned_pending") or [])
    manager = _manager_name()
    for t in pending:
        t["assigned_to"] = manager
    created, failed = await _create_tasks_batch(pending)
    await _delete_draft(draft_id)
    return {"ok": True, "created": created, "failed": failed, "assigned_to": manager}


async def start_await_username(draft_id: str) -> dict[str, Any]:
    payload = await _load_draft(draft_id)
    if not payload:
        return {"ok": False, "error": "Черновик не найден."}
    if not payload.get("unassigned_pending"):
        return {"ok": False, "error": "Нет задач без исполнителя."}
    payload["phase"] = "awaiting_username"
    await _update_draft(draft_id, payload)
    return {"ok": True}


async def apply_username_to_unassigned(draft_id: str, username: str) -> dict[str, Any]:
    payload = await _load_draft(draft_id)
    if not payload:
        return {"ok": False, "error": "Черновик не найден."}
    pending = list(payload.get("unassigned_pending") or [])
    if not pending:
        return {"ok": False, "error": "Нет задач без исполнителя."}

    u = username.strip().lstrip("@").lower()
    if not u:
        return {"ok": False, "error": "Укажите @username, например @asimhayatkhan"}

    from src.storage import get_store
    from src.utils.employee_role_resolve import resolve_employee_reference

    employees = await get_store().employees.list_employees()
    resolved = resolve_employee_reference(f"@{u}", employees)
    assignee = resolved.canonical_name if resolved.ok else u

    for t in pending:
        t["assigned_to"] = assignee
    created, failed = await _create_tasks_batch(pending)
    await _delete_draft(draft_id)
    return {"ok": True, "created": created, "failed": failed, "assigned_to": assignee}


async def skip_unassigned(draft_id: str) -> None:
    await _delete_draft(draft_id)


async def cancel_import(draft_id: str) -> None:
    await _delete_draft(draft_id)


async def try_handle_awaiting_username(message: Message) -> bool:
    """If user is replying with @username for pending draft, process and return True."""
    if not message.from_user:
        return False
    draft_id = await find_draft_awaiting_username(
        chat_id=int(message.chat.id),
        user_id=int(message.from_user.id),
    )
    if not draft_id:
        return False

    text = (message.text or "").strip()
    match = _AT_USERNAME.search(text)
    if not match:
        await message.answer(
            "Отправьте **@username** исполнителя одним сообщением, например: @asimhayatkhan"
        )
        return True

    username = match.group(1)
    result = await apply_username_to_unassigned(draft_id, username)
    if not result.get("ok"):
        await message.answer(str(result.get("error", "Ошибка")))
        return True

    from src.utils.creation_reply import format_created_tasks_lines

    created = result.get("created") or []
    failed = result.get("failed") or []
    assignee = str(result.get("assigned_to", "")).strip()
    reply = (
        f"✅ Создано задач без исполнителя: {len(created)} "
        f"(исполнитель: {assignee})."
    )
    link_lines = format_created_tasks_lines(created)
    if link_lines:
        reply += "\n\nПроверить в Google Tasks:\n" + "\n".join(link_lines)
    if failed:
        reply += f"\n⚠️ Не создано: {len(failed)}."
    await message.answer(reply)
    return True
