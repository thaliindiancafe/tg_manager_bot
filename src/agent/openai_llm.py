"""OpenAI Chat Completions agent (gpt-4o-mini) with tool calling."""

from __future__ import annotations

import json
import logging
from typing import Any

from openai import APIStatusError, AsyncOpenAI, RateLimitError

from src.agent import tools as agent_tools
from src.agent.checklist_proof import build_checklist_vision_prompt
from src.agent.prompt import SYSTEM_PROMPT
from src.config import settings
from src.google import sheets

logger = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 8
RETRY_DELAYS_SEC = (2, 4, 8)
PHOTO_VISION_PROMPT = (
    "Опиши содержимое фото. Если есть текст — извлеки его полностью. "
    "Если это задача или мероприятие — укажи это явно."
)

_client: AsyncOpenAI | None = None


def _get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(api_key=settings.openai_api_key)
    return _client


def _openai_tools() -> list[dict[str, Any]]:
    from src.agent.client import FUNCTION_DECLARATIONS

    return [
        {
            "type": "function",
            "function": {
                "name": decl["name"],
                "description": decl.get("description") or "",
                "parameters": decl.get("parameters") or {"type": "object", "properties": {}},
            },
        }
        for decl in FUNCTION_DECLARATIONS
    ]


def _normalize_role(role: str) -> str:
    value = (role or "user").strip().lower()
    if value in {"assistant", "bot", "model"}:
        return "assistant"
    return "user"


def _build_messages(
    history_messages: list[dict[str, Any]],
    user_message: str,
    system_instruction: str,
) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_instruction},
    ]
    for message in history_messages:
        role = _normalize_role(str(message.get("role", "user")))
        text = str(message.get("content", "")).strip()
        if text:
            messages.append({"role": role, "content": text})
    messages.append({"role": "user", "content": user_message.strip()})
    return messages


async def _chat_completion_with_retry(
    *,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
) -> Any:
    client = _get_client()
    last_error: BaseException | None = None
    max_attempts = len(RETRY_DELAYS_SEC) + 1

    for attempt in range(max_attempts):
        try:
            kwargs: dict[str, Any] = {
                "model": settings.openai_model,
                "messages": messages,
            }
            if tools:
                kwargs["tools"] = tools
                kwargs["tool_choice"] = "auto"
            return await client.chat.completions.create(**kwargs)
        except (RateLimitError, APIStatusError) as exc:
            last_error = exc
            status = getattr(exc, "status_code", None)
            if status in {429, 500, 502, 503, 504} and attempt < max_attempts - 1:
                delay = RETRY_DELAYS_SEC[attempt]
                logger.warning(
                    "OpenAI API %s, retry %s/%s in %ss",
                    status,
                    attempt + 1,
                    max_attempts - 1,
                    delay,
                )
                import asyncio

                await asyncio.sleep(delay)
                continue
            raise
        except Exception as exc:
            last_error = exc
            raise

    assert last_error is not None
    raise last_error


async def _generate_with_tools(messages: list[dict[str, Any]]) -> str:
    from src.agent.client import ERROR_MESSAGE, _execute_tool

    tools = _openai_tools()
    conversation = list(messages)

    for _ in range(MAX_TOOL_ROUNDS):
        response = await _chat_completion_with_retry(messages=conversation, tools=tools)
        choice = response.choices[0]
        assistant = choice.message

        if not assistant.tool_calls:
            return (assistant.content or "").strip() or ERROR_MESSAGE

        conversation.append(assistant.model_dump(exclude_none=True))

        for tool_call in assistant.tool_calls:
            fn = tool_call.function
            raw_args = fn.arguments or "{}"
            try:
                args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
            except json.JSONDecodeError:
                args = {}
            tool_result = await _execute_tool(fn.name, args)
            conversation.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": json.dumps(tool_result, ensure_ascii=False),
                }
            )

    return (assistant.content or "").strip() if assistant else ERROR_MESSAGE


async def run_agent(
    user_message: str,
    chat_id: int,
    history: list[dict],
    *,
    build_system_instruction,
    load_cross_chat_section,
    save_history,
) -> str:
    facts = await sheets.get_facts()
    sheet_history = await sheets.get_recent_history(chat_id, limit=30)
    merged_history = sheet_history
    if len(history) > len(sheet_history):
        merged_history = history

    history_messages = list(reversed(merged_history))
    cross_chat_section = await load_cross_chat_section(chat_id)
    staff_section = await agent_tools.build_staff_roles_section()
    system_instruction = build_system_instruction(
        facts, cross_chat_section, staff_section
    )
    messages = _build_messages(history_messages, user_message, system_instruction)

    reply = await _generate_with_tools(messages)
    try:
        await save_history(chat_id, user_message, reply)
    except Exception as exc:
        logger.warning(
            "memory_history save failed (reply still sent): chat_id=%s error=%s",
            chat_id,
            exc,
            exc_info=True,
        )
    return reply


async def evaluate_checklist_proof_text(proof_text: str, checklist: list[str]) -> str:
    if not checklist:
        return "[]"
    prompt = build_checklist_vision_prompt(checklist, proof_text)
    response = await _chat_completion_with_retry(
        messages=[{"role": "user", "content": prompt}],
    )
    return (response.choices[0].message.content or "").strip() or "[]"


async def describe_photo(image_base64: str, mime_type: str = "image/jpeg") -> str:
    data_url = f"data:{mime_type};base64,{image_base64}"
    response = await _chat_completion_with_retry(
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": PHOTO_VISION_PROMPT},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            }
        ],
    )
    return (
        response.choices[0].message.content or ""
    ).strip() or "Не удалось распознать содержимое фото."
