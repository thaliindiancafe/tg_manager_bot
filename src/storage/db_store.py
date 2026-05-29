"""Postgres-backed Store implementation."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import text

from src.db.engine import get_engine
from src.db.schema import ensure_schema
from src.storage.base import (
    AutomationsStore,
    ChatMessageRow,
    ChatMessagesStore,
    ChatsStore,
    EmployeesStore,
    KnowledgeStore,
    MemoryStore,
    ScheduleStore,
    Store,
    TasksStore,
)

logger = logging.getLogger(__name__)


async def _fetch_all(stmt: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    engine = get_engine()
    async with engine.connect() as conn:
        res = await conn.execute(text(stmt), params or {})
        rows = [dict(r._mapping) for r in res.fetchall()]
    return rows


async def _execute(stmt: str, params: dict[str, Any] | None = None) -> None:
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.execute(text(stmt), params or {})


class _DBEmployees(EmployeesStore):
    async def list_employees(self) -> list[dict[str, Any]]:
        return await _fetch_all(
            """
            select name, telegram_user_id, username, role, google_tasks_id,
                   case when active then 'true' else 'false' end as active
            from employees
            order by name asc
            """
        )

    async def upsert_employee(
        self,
        *,
        name: str,
        telegram_user_id: str = "",
        username: str = "",
        role: str = "",
        google_tasks_id: str = "",
        active: str = "true",
    ) -> None:
        await _execute(
            """
            insert into employees(name, telegram_user_id, username, role, google_tasks_id, active)
            values (:name, :telegram_user_id, :username, :role, :google_tasks_id, :active)
            on conflict (name, username) do update
              set telegram_user_id=excluded.telegram_user_id,
                  role=excluded.role,
                  google_tasks_id=excluded.google_tasks_id,
                  active=excluded.active,
                  updated_at=now()
            """,
            {
                "name": (name or "").strip(),
                "telegram_user_id": (telegram_user_id or "").strip(),
                "username": (username or "").strip().lstrip("@"),
                "role": (role or "").strip(),
                "google_tasks_id": (google_tasks_id or "").strip(),
                "active": str(active).strip().lower() != "false",
            },
        )

    async def bulk_upsert(self, rows: list[dict[str, str]]) -> dict[str, Any]:
        ok = 0
        failed: list[dict[str, Any]] = []
        total = len(rows or [])
        for r in rows or []:
            try:
                await self.upsert_employee(
                    name=str(r.get("name", "")).strip(),
                    telegram_user_id=str(r.get("telegram_user_id", "")).strip(),
                    username=str(r.get("username", "")).strip(),
                    role=str(r.get("role", "")).strip(),
                    google_tasks_id=str(r.get("google_tasks_id", "")).strip(),
                    active=str(r.get("active", "true")).strip(),
                )
                ok += 1
            except Exception as exc:
                failed.append({"name": r.get("name") or "?", "error": str(exc)})
        return {"ok_count": ok, "total_parsed": total, "failed": failed}


class _DBTasks(TasksStore):
    async def list_tasks(self) -> list[dict[str, Any]]:
        return await _fetch_all(
            """
            select task_id, title, assigned_to, due_date, status,
                   reminder_count::text as reminder_count,
                   notes, google_task_id, google_tasklist_id
            from tasks
            order by created_at desc
            """
        )

    async def upsert_task(self, row: dict[str, Any]) -> None:
        await _execute(
            """
            insert into tasks(task_id, title, assigned_to, due_date, status, reminder_count, notes,
                              google_task_id, google_tasklist_id)
            values (:task_id, :title, :assigned_to, :due_date, :status, :reminder_count, :notes,
                    :google_task_id, :google_tasklist_id)
            on conflict (task_id) do update
              set title=excluded.title,
                  assigned_to=excluded.assigned_to,
                  due_date=excluded.due_date,
                  status=excluded.status,
                  reminder_count=excluded.reminder_count,
                  notes=excluded.notes,
                  google_task_id=excluded.google_task_id,
                  google_tasklist_id=excluded.google_tasklist_id,
                  updated_at=now()
            """,
            {
                "task_id": str(row.get("task_id", "")).strip(),
                "title": str(row.get("title", "")).strip(),
                "assigned_to": str(row.get("assigned_to", "")).strip(),
                "due_date": str(row.get("due_date", "")).strip(),
                "status": str(row.get("status", "")).strip(),
                "reminder_count": int(str(row.get("reminder_count", "0")).strip() or "0"),
                "notes": str(row.get("notes", "")).strip(),
                "google_task_id": str(row.get("google_task_id", "")).strip(),
                "google_tasklist_id": str(row.get("google_tasklist_id", "")).strip(),
            },
        )

    async def update_task_fields(self, task_id: str, fields: dict[str, Any]) -> None:
        if not fields:
            return
        # Safe whitelist
        allowed = {"title", "assigned_to", "due_date", "status", "reminder_count", "notes", "google_task_id", "google_tasklist_id"}
        updates = {k: v for k, v in fields.items() if k in allowed}
        if not updates:
            return
        set_fragments = []
        params: dict[str, Any] = {"task_id": str(task_id).strip()}
        for idx, (k, v) in enumerate(updates.items()):
            key = f"v{idx}"
            set_fragments.append(f"{k} = :{key}")
            params[key] = v
        set_sql = ", ".join(set_fragments) + ", updated_at=now()"
        await _execute(f"update tasks set {set_sql} where task_id=:task_id", params)


class _DBMemory(MemoryStore):
    async def list_facts(self) -> list[dict[str, Any]]:
        return await _fetch_all("select employee, fact, created_at from memory_facts order by employee asc")

    async def upsert_fact(self, employee: str, fact: str) -> None:
        now = datetime.now().isoformat()
        await _execute(
            """
            insert into memory_facts(employee, fact, created_at)
            values (:employee, :fact, :created_at)
            on conflict (employee) do update
              set fact=excluded.fact,
                  created_at=excluded.created_at
            """,
            {"employee": employee, "fact": fact, "created_at": now},
        )

    async def append_history(self, chat_id: int, role: str, content: str, timestamp: str) -> None:
        await _execute(
            "insert into memory_history(chat_id, role, content, timestamp) values (:chat_id,:role,:content,:timestamp)",
            {"chat_id": int(chat_id), "role": role, "content": content, "timestamp": timestamp},
        )

    async def get_recent_history(self, chat_id: int, limit: int = 30) -> list[dict[str, Any]]:
        return await _fetch_all(
            """
            select chat_id::text as chat_id, role, content, timestamp
            from memory_history
            where chat_id=:chat_id
            order by id desc
            limit :limit
            """,
            {"chat_id": int(chat_id), "limit": int(limit)},
        )

    async def get_recent_history_other_chats(
        self, *, exclude_chat_id: int, limit: int
    ) -> list[dict[str, Any]]:
        return await _fetch_all(
            """
            select chat_id::text as chat_id, role, content, timestamp
            from memory_history
            where chat_id != :exclude
            order by id desc
            limit :limit
            """,
            {"exclude": int(exclude_chat_id), "limit": int(limit)},
        )


class _DBKnowledge(KnowledgeStore):
    async def list_sources(self) -> list[dict[str, Any]]:
        return await _fetch_all("select * from knowledge_sources order by indexed_at desc")

    async def list_chunks(self) -> list[dict[str, Any]]:
        return await _fetch_all("select * from knowledge_chunks order by source_id, chunk_index")

    async def upsert_source(self, row: dict[str, Any]) -> None:
        await _execute(
            """
            insert into knowledge_sources(source_id, drive_file_id, title, mime_type, content_hash, indexed_at,
                                          active, chunk_count, error)
            values (:source_id,:drive_file_id,:title,:mime_type,:content_hash,:indexed_at,:active,:chunk_count,:error)
            on conflict (source_id) do update set
              drive_file_id=excluded.drive_file_id,
              title=excluded.title,
              mime_type=excluded.mime_type,
              content_hash=excluded.content_hash,
              indexed_at=excluded.indexed_at,
              active=excluded.active,
              chunk_count=excluded.chunk_count,
              error=excluded.error
            """,
            {
                "source_id": str(row.get("source_id", "")).strip(),
                "drive_file_id": str(row.get("drive_file_id", "")).strip(),
                "title": str(row.get("title", "")).strip(),
                "mime_type": str(row.get("mime_type", "")).strip(),
                "content_hash": str(row.get("content_hash", "")).strip(),
                "indexed_at": str(row.get("indexed_at", "")).strip(),
                "active": str(row.get("active", "true")).strip().lower() != "false",
                "chunk_count": int(str(row.get("chunk_count", "0")).strip() or "0"),
                "error": str(row.get("error", "")).strip(),
            },
        )

    async def replace_chunks_for_source(self, source_id: str, chunks: list[dict[str, Any]]) -> None:
        sid = str(source_id).strip()
        await _execute("delete from knowledge_chunks where source_id=:sid", {"sid": sid})
        for ch in chunks or []:
            await _execute(
                "insert into knowledge_chunks(source_id, chunk_index, text) values (:sid,:idx,:text)",
                {"sid": sid, "idx": int(ch.get("chunk_index", 0)), "text": str(ch.get("text", "")).strip()},
            )


class _DBSchedule(ScheduleStore):
    async def list_schedule(self) -> list[dict[str, Any]]:
        return await _fetch_all(
            "select date, employee, role, shift_start, shift_end, telegram_user_id from schedule order by date asc"
        )

    async def replace_schedule_rows(self, rows: list[dict[str, Any]]) -> None:
        await _execute("delete from schedule")
        for r in rows or []:
            await _execute(
                """
                insert into schedule(date, employee, role, shift_start, shift_end, telegram_user_id)
                values (:date,:employee,:role,:shift_start,:shift_end,:telegram_user_id)
                """,
                {
                    "date": str(r.get("date", "")).strip(),
                    "employee": str(r.get("employee", "")).strip(),
                    "role": str(r.get("role", "")).strip(),
                    "shift_start": str(r.get("shift_start", "")).strip(),
                    "shift_end": str(r.get("shift_end", "")).strip(),
                    "telegram_user_id": str(r.get("telegram_user_id", "")).strip(),
                },
            )


class _DBAutomations(AutomationsStore):
    async def list_automations(self) -> list[dict[str, Any]]:
        return await _fetch_all(
            """
            select id, trigger_type, trigger_time, trigger_day, action, params,
                   case when active then 'true' else 'false' end as active
            from automations
            order by id asc
            """
        )

    async def upsert_automation(self, row: dict[str, Any]) -> None:
        await _execute(
            """
            insert into automations(id, trigger_type, trigger_time, trigger_day, action, params, active)
            values (:id,:trigger_type,:trigger_time,:trigger_day,:action,:params,:active)
            on conflict (id) do update set
              trigger_type=excluded.trigger_type,
              trigger_time=excluded.trigger_time,
              trigger_day=excluded.trigger_day,
              action=excluded.action,
              params=excluded.params,
              active=excluded.active
            """,
            {
                "id": str(row.get("id", "")).strip(),
                "trigger_type": str(row.get("trigger_type", "")).strip(),
                "trigger_time": str(row.get("trigger_time", "")).strip(),
                "trigger_day": str(row.get("trigger_day", "")).strip(),
                "action": str(row.get("action", "")).strip(),
                "params": str(row.get("params", "")).strip(),
                "active": str(row.get("active", "true")).strip().lower() != "false",
            },
        )


class _DBChats(ChatsStore):
    async def list_chats(self) -> list[dict[str, Any]]:
        return await _fetch_all(
            """
            select chat_id::text as chat_id, chat_name,
                   case when active then 'true' else 'false' end as active,
                   timezone
            from chats
            order by chat_id asc
            """
        )

    async def upsert_chat(self, row: dict[str, Any]) -> None:
        await _execute(
            """
            insert into chats(chat_id, chat_name, active, timezone)
            values (:chat_id,:chat_name,:active,:timezone)
            on conflict (chat_id) do update set
              chat_name=excluded.chat_name,
              active=excluded.active,
              timezone=excluded.timezone
            """,
            {
                "chat_id": int(str(row.get("chat_id", "0")).strip() or "0"),
                "chat_name": str(row.get("chat_name", "")).strip(),
                "active": str(row.get("active", "true")).strip().lower() != "false",
                "timezone": str(row.get("timezone", "")).strip(),
            },
        )


class _DBChatMessages(ChatMessagesStore):
    async def append_chat_message(self, row: ChatMessageRow) -> None:
        await _execute(
            """
            insert into chat_messages(chat_id, message_id, user_id, username, full_name, text, created_at)
            values (:chat_id,:message_id,:user_id,:username,:full_name,:text,:created_at)
            on conflict (chat_id, message_id) do nothing
            """,
            {
                "chat_id": int(row.chat_id),
                "message_id": int(row.message_id),
                "user_id": int(row.user_id) if row.user_id is not None else None,
                "username": (row.username or "").strip(),
                "full_name": (row.full_name or "").strip(),
                "text": row.text,
                "created_at": row.created_at,
            },
        )

    async def list_chat_messages_since(
        self, *, chat_id: int, since_dt: datetime, limit: int
    ) -> list[ChatMessageRow]:
        rows = await _fetch_all(
            """
            select chat_id, message_id, user_id, username, full_name, text, created_at
            from chat_messages
            where chat_id=:chat_id and created_at >= :since_dt
            order by created_at asc
            limit :limit
            """,
            {"chat_id": int(chat_id), "since_dt": since_dt, "limit": int(limit)},
        )
        out: list[ChatMessageRow] = []
        for r in rows:
            out.append(
                ChatMessageRow(
                    chat_id=int(r["chat_id"]),
                    message_id=int(r["message_id"]),
                    user_id=int(r["user_id"]) if r.get("user_id") is not None else None,
                    username=str(r.get("username", "") or ""),
                    full_name=str(r.get("full_name", "") or ""),
                    text=str(r.get("text", "") or ""),
                    created_at=r["created_at"],
                )
            )
        return out


@dataclass
class DBStore(Store):
    employees: EmployeesStore = _DBEmployees()
    tasks: TasksStore = _DBTasks()
    memory: MemoryStore = _DBMemory()
    knowledge: KnowledgeStore = _DBKnowledge()
    schedule: ScheduleStore = _DBSchedule()
    automations: AutomationsStore = _DBAutomations()
    chats: ChatsStore = _DBChats()
    chat_messages: ChatMessagesStore = _DBChatMessages()

    _initialized: bool = False

    def __post_init__(self) -> None:
        # Lazy schema init; caller should await init() during startup.
        pass

    async def init(self) -> None:
        if self._initialized:
            return
        await ensure_schema()
        self._initialized = True
        logger.info("DBStore initialized")

