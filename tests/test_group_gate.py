"""Tests for group chat mention gate."""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock

from aiogram.enums import ChatType
from aiogram.types import Chat, Message, User

from src.bot import group_gate


@pytest.mark.asyncio
async def test_private_always_allowed(monkeypatch):
    monkeypatch.setattr(group_gate.settings, "group_agent_require_mention", True)
    msg = MagicMock(spec=Message)
    msg.chat = Chat(id=1, type=ChatType.PRIVATE)
    assert await group_gate.should_process_group_message(msg) is True


@pytest.mark.asyncio
async def test_group_without_mention_skipped(monkeypatch):
    monkeypatch.setattr(group_gate.settings, "group_agent_require_mention", True)
    bot = AsyncMock()
    bot.me.return_value = User(id=99, is_bot=True, first_name="Bot", username="thali_manager_bot")
    msg = MagicMock(spec=Message)
    msg.chat = Chat(id=-100, type=ChatType.SUPERGROUP)
    msg.text = "Асим, сделай замену до пятницы"
    msg.caption = None
    msg.entities = []
    msg.caption_entities = []
    msg.reply_to_message = None
    msg.bot = bot

    assert await group_gate.should_process_group_message(msg) is False


@pytest.mark.asyncio
async def test_group_with_mention_allowed(monkeypatch):
    monkeypatch.setattr(group_gate.settings, "group_agent_require_mention", True)
    bot = AsyncMock()
    bot.me.return_value = User(id=99, is_bot=True, first_name="Bot", username="thali_manager_bot")
    msg = MagicMock(spec=Message)
    msg.chat = Chat(id=-100, type=ChatType.SUPERGROUP)
    msg.text = "@thali_manager_bot поручи Асиму замену"
    msg.caption = None
    msg.entities = []
    msg.caption_entities = []
    msg.reply_to_message = None
    msg.bot = bot

    assert await group_gate.should_process_group_message(msg) is True
