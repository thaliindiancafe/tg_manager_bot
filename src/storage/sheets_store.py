"""Sheets-backed Store implementation (adapter over src.google.sheets)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from src.google import sheets
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


class _SheetsEmployees(EmployeesStore):
    async def list_employees(self) -> list[dict[str, Any]]:
        return await sheets.read_sheet("employees")

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
        await sheets.upsert_employee_row(
            {
                "name": name,
                "telegram_user_id": telegram_user_id,
                "username": username,
                "role": role,
                "google_tasks_id": google_tasks_id,
                "active": active,
            }
        )

    async def bulk_upsert(self, rows: list[dict[str, str]]) -> dict[str, Any]:
        return await sheets.bulk_upsert_employees(rows)


class _SheetsTasks(TasksStore):
    async def list_tasks(self) -> list[dict[str, Any]]:
        return await sheets.read_sheet("tasks")

    async def upsert_task(self, row: dict[str, Any]) -> None:
        await sheets.upsert_task_row(row)

    async def update_task_fields(self, task_id: str, fields: dict[str, Any]) -> None:
        await sheets.update_task_fields(task_id, fields)


class _SheetsMemory(MemoryStore):
    async def list_facts(self) -> list[dict[str, Any]]:
        return await sheets.read_sheet("memory_facts")

    async def upsert_fact(self, employee: str, fact: str) -> None:
        await sheets.upsert_fact(employee=employee, fact=fact)

    async def append_history(self, chat_id: int, role: str, content: str, timestamp: str) -> None:
        await sheets.append_row(
            "memory_history",
            {"chat_id": str(chat_id), "role": role, "content": content, "timestamp": timestamp},
        )

    async def get_recent_history(self, chat_id: int, limit: int = 30) -> list[dict[str, Any]]:
        return await sheets.get_recent_history(chat_id, limit=limit)

    async def get_recent_history_other_chats(
        self, *, exclude_chat_id: int, limit: int
    ) -> list[dict[str, Any]]:
        return await sheets.get_recent_history_other_chats(exclude_chat_id, limit=limit)


class _SheetsKnowledge(KnowledgeStore):
    async def list_sources(self) -> list[dict[str, Any]]:
        return await sheets.read_sheet("knowledge_sources")

    async def list_chunks(self) -> list[dict[str, Any]]:
        return await sheets.read_sheet("knowledge_chunks")

    async def upsert_source(self, row: dict[str, Any]) -> None:
        await sheets.upsert_knowledge_source(row)

    async def replace_chunks_for_source(self, source_id: str, chunks: list[dict[str, Any]]) -> None:
        await sheets.replace_knowledge_chunks(source_id, chunks)


class _SheetsSchedule(ScheduleStore):
    async def list_schedule(self) -> list[dict[str, Any]]:
        return await sheets.read_sheet("schedule")

    async def replace_schedule_rows(self, rows: list[dict[str, Any]]) -> None:
        await sheets.replace_schedule_rows(rows)


class _SheetsAutomations(AutomationsStore):
    async def list_automations(self) -> list[dict[str, Any]]:
        return await sheets.read_sheet("automations")

    async def upsert_automation(self, row: dict[str, Any]) -> None:
        await sheets.upsert_automation_row(row)


class _SheetsChats(ChatsStore):
    async def list_chats(self) -> list[dict[str, Any]]:
        return await sheets.read_sheet("chats")

    async def upsert_chat(self, row: dict[str, Any]) -> None:
        await sheets.upsert_chat_row(row)


class _NoopChatMessages(ChatMessagesStore):
    async def append_chat_message(self, row: ChatMessageRow) -> None:
        return None

    async def list_chat_messages_since(
        self, *, chat_id: int, since_dt: datetime, limit: int
    ) -> list[ChatMessageRow]:
        return []


@dataclass
class SheetsStore(Store):
    employees: EmployeesStore = _SheetsEmployees()
    tasks: TasksStore = _SheetsTasks()
    memory: MemoryStore = _SheetsMemory()
    knowledge: KnowledgeStore = _SheetsKnowledge()
    schedule: ScheduleStore = _SheetsSchedule()
    automations: AutomationsStore = _SheetsAutomations()
    chats: ChatsStore = _SheetsChats()
    chat_messages: ChatMessagesStore = _NoopChatMessages()

