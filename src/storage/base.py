"""Storage abstraction (Sheets or DB)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol


@dataclass(frozen=True)
class ChatMessageRow:
    chat_id: int
    message_id: int
    user_id: int | None
    username: str
    full_name: str
    text: str
    created_at: datetime


class EmployeesStore(Protocol):
    async def list_employees(self) -> list[dict[str, Any]]: ...

    async def upsert_employee(
        self,
        *,
        name: str,
        telegram_user_id: str = "",
        username: str = "",
        role: str = "",
        google_tasks_id: str = "",
        active: str = "true",
    ) -> None: ...

    async def bulk_upsert(self, rows: list[dict[str, str]]) -> dict[str, Any]: ...


class TasksStore(Protocol):
    async def list_tasks(self) -> list[dict[str, Any]]: ...

    async def upsert_task(self, row: dict[str, Any]) -> None: ...

    async def update_task_fields(self, task_id: str, fields: dict[str, Any]) -> None: ...


class MemoryStore(Protocol):
    async def list_facts(self) -> list[dict[str, Any]]: ...

    async def upsert_fact(self, employee: str, fact: str) -> None: ...

    async def append_history(self, chat_id: int, role: str, content: str, timestamp: str) -> None: ...

    async def get_recent_history(self, chat_id: int, limit: int = 30) -> list[dict[str, Any]]: ...

    async def get_recent_history_other_chats(
        self,
        *,
        exclude_chat_id: int,
        limit: int,
    ) -> list[dict[str, Any]]: ...


class KnowledgeStore(Protocol):
    async def list_sources(self) -> list[dict[str, Any]]: ...

    async def list_chunks(self) -> list[dict[str, Any]]: ...

    async def upsert_source(self, row: dict[str, Any]) -> None: ...

    async def replace_chunks_for_source(self, source_id: str, chunks: list[dict[str, Any]]) -> None: ...


class ScheduleStore(Protocol):
    async def list_schedule(self) -> list[dict[str, Any]]: ...

    async def replace_schedule_rows(self, rows: list[dict[str, Any]]) -> None: ...


class AutomationsStore(Protocol):
    async def list_automations(self) -> list[dict[str, Any]]: ...

    async def upsert_automation(self, row: dict[str, Any]) -> None: ...


class ChatsStore(Protocol):
    async def list_chats(self) -> list[dict[str, Any]]: ...

    async def upsert_chat(self, row: dict[str, Any]) -> None: ...


class ChatMessagesStore(Protocol):
    async def append_chat_message(self, row: ChatMessageRow) -> None: ...

    async def list_chat_messages_since(
        self, *, chat_id: int, since_dt: datetime, limit: int
    ) -> list[ChatMessageRow]: ...


class Store(Protocol):
    employees: EmployeesStore
    tasks: TasksStore
    memory: MemoryStore
    knowledge: KnowledgeStore
    schedule: ScheduleStore
    automations: AutomationsStore
    chats: ChatsStore
    chat_messages: ChatMessagesStore

