"""DB schema (DDL) and init helpers.

We keep it intentionally simple (plain SQL) to avoid bringing a full migration tool
until the product surface stabilizes.
"""

from __future__ import annotations

import logging

from sqlalchemy import text

from src.db.engine import get_engine

logger = logging.getLogger(__name__)


DDL = [
    # Core tables
    """
    create table if not exists employees (
      id bigserial primary key,
      name text not null,
      telegram_user_id text not null default '',
      username text not null default '',
      role text not null default '',
      google_tasks_id text not null default '',
      active boolean not null default true,
      updated_at timestamptz not null default now(),
      unique (name, username)
    );
    """,
    """
    create table if not exists tasks (
      task_id text primary key,
      title text not null,
      assigned_to text not null default '',
      due_date text not null default '',
      status text not null default '',
      reminder_count integer not null default 0,
      notes text not null default '',
      google_task_id text not null default '',
      google_tasklist_id text not null default '',
      created_at timestamptz not null default now(),
      updated_at timestamptz not null default now()
    );
    """,
    """
    create table if not exists memory_facts (
      employee text primary key,
      fact text not null,
      created_at text not null
    );
    """,
    """
    create table if not exists memory_history (
      id bigserial primary key,
      chat_id bigint not null,
      role text not null,
      content text not null,
      timestamp text not null
    );
    """,
    """
    create index if not exists idx_memory_history_chat_ts
      on memory_history (chat_id, id desc);
    """,
    """
    create table if not exists schedule (
      id bigserial primary key,
      date text not null,
      employee text not null,
      role text not null default '',
      shift_start text not null default '',
      shift_end text not null default '',
      telegram_user_id text not null default ''
    );
    """,
    """
    create index if not exists idx_schedule_date on schedule (date);
    """,
    """
    create table if not exists automations (
      id text primary key,
      trigger_type text not null,
      trigger_time text not null default '',
      trigger_day text not null default '',
      action text not null,
      params text not null default '',
      active boolean not null default true
    );
    """,
    """
    create table if not exists chats (
      chat_id bigint primary key,
      chat_name text not null default '',
      active boolean not null default true,
      timezone text not null default ''
    );
    """,
    """
    create table if not exists knowledge_sources (
      source_id text primary key,
      drive_file_id text not null default '',
      title text not null default '',
      mime_type text not null default '',
      content_hash text not null default '',
      indexed_at text not null default '',
      active boolean not null default true,
      chunk_count integer not null default 0,
      error text not null default ''
    );
    """,
    """
    create table if not exists knowledge_chunks (
      source_id text not null,
      chunk_index integer not null,
      text text not null,
      primary key (source_id, chunk_index)
    );
    """,
    # Mira-like: log all chat messages (group transcript)
    """
    create table if not exists chat_messages (
      id bigserial primary key,
      chat_id bigint not null,
      message_id bigint not null,
      user_id bigint null,
      username text not null default '',
      full_name text not null default '',
      text text not null,
      created_at timestamptz not null default now(),
      unique (chat_id, message_id)
    );
    """,
    """
    create index if not exists idx_chat_messages_chat_created
      on chat_messages (chat_id, created_at desc);
    """,
    # Safe mode: task-import drafts awaiting confirmation
    """
    create table if not exists task_import_drafts (
      draft_id text primary key,
      chat_id bigint not null,
      created_by_user_id bigint null,
      created_at timestamptz not null default now(),
      payload_json text not null
    );
    """,
    """
    create index if not exists idx_task_import_drafts_chat
      on task_import_drafts (chat_id, created_at desc);
    """,
]


async def ensure_schema() -> None:
    engine = get_engine()
    async with engine.begin() as conn:
        for stmt in DDL:
            await conn.execute(text(stmt))
    logger.info("DB schema ensured")

