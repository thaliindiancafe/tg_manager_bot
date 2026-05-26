"""Gemini agent client with tool-use loop."""

from __future__ import annotations

import asyncio
import base64
import inspect
import json
import logging
from datetime import datetime
from typing import Any, Callable
from zoneinfo import ZoneInfo

from google import genai
from google.genai import errors as genai_errors
from google.genai import types

from src.agent.checklist_proof import build_checklist_vision_prompt
from src.agent.cross_chat_memory import build_cross_chat_system_section
from src.agent.prompt import SYSTEM_PROMPT
from src.agent import tools as agent_tools
from src.config import settings, use_openai_llm
from src.google import sheets
from src.utils.telegram_reply import format_bot_reply

logger = logging.getLogger(__name__)

MODEL_ID = "gemini-2.5-flash-lite"
MAX_TOOL_ROUNDS = 8
ERROR_MESSAGE = "Произошла ошибка, попробуй ещё раз"
OVERLOAD_MESSAGE = (
    "Сервис ИИ временно перегружен. Подожди 10–20 секунд и напиши снова."
)
QUOTA_EXHAUSTED_MESSAGE = (
    "Лимит запросов к Gemini API на сегодня исчерпан (бесплатный тариф ~20 в день).\n\n"
    "Без ИИ уже работают:\n"
    "• «задачи у Иры на завтра»\n"
    "• «кто завтра на смене»\n"
    "• «встречи в календаре на завтра» (после python scripts/google_tasks_oauth_setup.py)\n\n"
    "Для свободного диалога: включите биллинг в Google AI Studio "
    "(aistudio.google.com) или подождите сброс лимита (~полночь по тихоокеанскому времени Google)."
)
GEMINI_RETRY_DELAYS_SEC = (3, 6, 12, 24)
GEMINI_RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})
PHOTO_VISION_PROMPT = (
    "Опиши содержимое фото. Если есть текст — извлеки его полностью. "
    "Если это задача или мероприятие — укажи это явно."
)

_client: genai.Client | None = None

FUNCTION_DECLARATIONS: list[dict[str, Any]] = [
    {
        "name": "get_schedule_for_dates",
        "description": inspect.getdoc(agent_tools.get_schedule_for_dates) or "",
        "parameters": {
            "type": "object",
            "properties": {
                "preset": {
                    "type": "string",
                    "enum": ["today", "tomorrow", "yesterday", "none"],
                    "description": (
                        "Относительный день в часовом поясе ресторана. "
                        "Для «сегодня на смене» — today; «завтра» — tomorrow; «вчера» — yesterday. "
                        "none — только если задаёшь явные даты в dates."
                    ),
                },
                "dates": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Дополнительные даты YYYY-MM-DD (можно несколько).",
                },
            },
        },
    },
    {
        "name": "get_tasks_for_dates",
        "description": inspect.getdoc(agent_tools.get_tasks_for_dates) or "",
        "parameters": {
            "type": "object",
            "properties": {
                "preset": {
                    "type": "string",
                    "enum": ["today", "tomorrow", "yesterday", "none"],
                    "description": (
                        "today, tomorrow, yesterday или none с явными dates."
                    ),
                },
                "dates": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Дополнительные даты YYYY-MM-DD.",
                },
            },
        },
    },
    {
        "name": "get_employee_tasks_for_dates",
        "description": inspect.getdoc(agent_tools.get_employee_tasks_for_dates) or "",
        "parameters": {
            "type": "object",
            "properties": {
                "employee_name": {
                    "type": "string",
                    "description": "Имя как в employees.name (Ира, Августа…)",
                },
                "preset": {
                    "type": "string",
                    "enum": ["today", "tomorrow", "yesterday", "none"],
                },
                "dates": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Дополнительные даты YYYY-MM-DD.",
                },
            },
            "required": ["employee_name"],
        },
    },
    {
        "name": "get_employee_tasks",
        "description": inspect.getdoc(agent_tools.get_employee_tasks) or "",
        "parameters": {
            "type": "object",
            "properties": {
                "employee_name": {
                    "type": "string",
                    "description": "Имя сотрудника",
                }
            },
            "required": ["employee_name"],
        },
    },
    {
        "name": "get_open_tasks_for_telegram_user",
        "description": inspect.getdoc(
            agent_tools.get_open_tasks_for_telegram_user
        )
        or "",
        "parameters": {
            "type": "object",
            "properties": {
                "telegram_user_id": {
                    "type": "integer",
                    "description": "Числовой Telegram ID пользователя (из контекста лички)",
                },
            },
            "required": ["telegram_user_id"],
        },
    },
    {
        "name": "assert_task_for_telegram_user",
        "description": inspect.getdoc(agent_tools.assert_task_for_telegram_user) or "",
        "parameters": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "UUID задачи из реплая"},
                "telegram_user_id": {
                    "type": "integer",
                    "description": "Telegram ID того, кто отчитывается (из контекста лички)",
                },
            },
            "required": ["task_id", "telegram_user_id"],
        },
    },
    {
        "name": "complete_task",
        "description": inspect.getdoc(agent_tools.complete_task) or "",
        "parameters": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "ID задачи"},
            },
            "required": ["task_id"],
        },
    },
    {
        "name": "submit_task_proof",
        "description": inspect.getdoc(agent_tools.submit_task_proof) or "",
        "parameters": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "UUID задачи"},
                "proof_description": {
                    "type": "string",
                    "description": "Текст отчёта или описание фото",
                },
                "telegram_user_id": {
                    "type": "integer",
                    "description": "Telegram ID сотрудника (из контекста лички)",
                },
            },
            "required": ["task_id", "proof_description", "telegram_user_id"],
        },
    },
    {
        "name": "approve_task",
        "description": inspect.getdoc(agent_tools.approve_task) or "",
        "parameters": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "UUID задачи на проверке"},
            },
            "required": ["task_id"],
        },
    },
    {
        "name": "reject_task_proof",
        "description": inspect.getdoc(agent_tools.reject_task_proof) or "",
        "parameters": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "UUID задачи"},
                "comment": {
                    "type": "string",
                    "description": "Комментарий руководителя для доработки",
                },
            },
            "required": ["task_id"],
        },
    },
    {
        "name": "postpone_task",
        "description": inspect.getdoc(agent_tools.postpone_task) or "",
        "parameters": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "ID задачи"},
                "new_due_date": {
                    "type": "string",
                    "description": "Новая дата YYYY-MM-DD",
                },
            },
            "required": ["task_id", "new_due_date"],
        },
    },
    {
        "name": "create_task",
        "description": inspect.getdoc(agent_tools.create_task) or "",
        "parameters": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "assigned_to": {
                    "type": "string",
                    "description": "Имя, @username или должность (шеф, су-шеф)",
                },
                "due_date": {"type": "string", "description": "YYYY-MM-DD"},
                "notes": {"type": "string"},
            },
            "required": ["title", "assigned_to", "due_date", "notes"],
        },
    },
    {
        "name": "create_event",
        "description": inspect.getdoc(agent_tools.create_event) or "",
        "parameters": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "date": {"type": "string", "description": "YYYY-MM-DD"},
                "time": {"type": "string", "description": "HH:MM"},
                "description": {"type": "string"},
            },
            "required": ["title", "date", "time", "description"],
        },
    },
    {
        "name": "get_events_for_dates",
        "description": inspect.getdoc(agent_tools.get_events_for_dates) or "",
        "parameters": {
            "type": "object",
            "properties": {
                "preset": {
                    "type": "string",
                    "enum": ["today", "tomorrow", "yesterday", "none"],
                    "description": (
                        "Относительный день: today, tomorrow, yesterday. "
                        "none — только явные даты в dates."
                    ),
                },
                "dates": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Дополнительные даты YYYY-MM-DD.",
                },
            },
        },
    },
    {
        "name": "get_today_events",
        "description": inspect.getdoc(agent_tools.get_today_events) or "",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "save_automation",
        "description": inspect.getdoc(agent_tools.save_automation) or "",
        "parameters": {
            "type": "object",
            "properties": {
                "trigger_type": {"type": "string"},
                "trigger_time": {"type": "string"},
                "trigger_day": {"type": "string"},
                "action": {"type": "string"},
                "params": {"type": "string"},
            },
            "required": [
                "trigger_type",
                "trigger_time",
                "trigger_day",
                "action",
                "params",
            ],
        },
    },
    {
        "name": "read_drive_document",
        "description": inspect.getdoc(agent_tools.read_drive_document) or "",
        "parameters": {
            "type": "object",
            "properties": {
                "file_id": {"type": "string", "description": "ID файла Google Drive"},
            },
            "required": ["file_id"],
        },
    },
    {
        "name": "search_knowledge",
        "description": inspect.getdoc(agent_tools.search_knowledge) or "",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Вопрос или ключевые слова для поиска по базе знаний",
                },
                "top_k": {
                    "type": "integer",
                    "description": "Сколько фрагментов вернуть (по умолчанию из настроек)",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "sync_knowledge_folder",
        "description": inspect.getdoc(agent_tools.sync_knowledge_folder) or "",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "list_knowledge_sources",
        "description": inspect.getdoc(agent_tools.list_knowledge_sources) or "",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "register_employee",
        "description": inspect.getdoc(agent_tools.register_employee) or "",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "username": {
                    "type": "string",
                    "description": "@username с @ или без",
                },
                "role": {"type": "string"},
                "telegram_user_id": {
                    "type": "integer",
                    "description": "0 если ID неизвестен",
                },
            },
            "required": ["name"],
        },
    },
    {
        "name": "register_employees_bulk",
        "description": inspect.getdoc(agent_tools.register_employees_bulk) or "",
        "parameters": {
            "type": "object",
            "properties": {
                "employees_text": {
                    "type": "string",
                    "description": (
                        "Полный текст со списком: «Имя, должность - @username» по строкам"
                    ),
                },
            },
            "required": ["employees_text"],
        },
    },
    {
        "name": "delegate_private_reminder",
        "description": inspect.getdoc(agent_tools.delegate_private_reminder) or "",
        "parameters": {
            "type": "object",
            "properties": {
                "employee_name": {
                    "type": "string",
                    "description": "Имя, @username или должность (шеф, су-шеф)",
                },
                "title": {"type": "string"},
                "message_to_employee": {"type": "string"},
                "due_date": {
                    "type": "string",
                    "description": "YYYY-MM-DD",
                },
                "notes_for_task": {
                    "type": "string",
                    "description": "Доп. текст в задачу (чеклист, контекст); можно пустым",
                },
                "checklist_items": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Пункты чеклиста для notes (__DELEGATION_JSON__); можно не передавать",
                },
            },
            "required": [
                "employee_name",
                "title",
                "message_to_employee",
                "due_date",
            ],
        },
    },
    {
        "name": "send_dm_to_employee",
        "description": inspect.getdoc(agent_tools.send_dm_to_employee) or "",
        "parameters": {
            "type": "object",
            "properties": {
                "employee_name": {
                    "type": "string",
                    "description": "Имя, @username или должность (шеф, су-шеф) — см. employees",
                },
                "message_text": {
                    "type": "string",
                    "description": "Текст личного сообщения сотруднику",
                },
            },
            "required": ["employee_name", "message_text"],
        },
    },
    {
        "name": "send_brief_to_primary_work_chat",
        "description": inspect.getdoc(agent_tools.send_brief_to_primary_work_chat)
        or "",
        "parameters": {
            "type": "object",
            "properties": {
                "message_text": {
                    "type": "string",
                    "description": "Краткий текст в первый активный чат из chats (макс. ~450 символов)",
                },
            },
            "required": ["message_text"],
        },
    },
    {
        "name": "get_employee_directory",
        "description": inspect.getdoc(agent_tools.get_employee_directory) or "",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "save_fact",
        "description": inspect.getdoc(agent_tools.save_fact) or "",
        "parameters": {
            "type": "object",
            "properties": {
                "fact": {"type": "string"},
                "employee": {
                    "type": "string",
                    "description": "Имя сотрудника, если факт про человека",
                },
            },
            "required": ["fact"],
        },
    },
]

TOOL_REGISTRY: dict[str, Callable[..., Any]] = {
    "get_schedule_for_dates": agent_tools.get_schedule_for_dates,
    "get_tasks_for_dates": agent_tools.get_tasks_for_dates,
    "get_employee_tasks_for_dates": agent_tools.get_employee_tasks_for_dates,
    "get_employee_tasks": agent_tools.get_employee_tasks,
    "get_open_tasks_for_telegram_user": agent_tools.get_open_tasks_for_telegram_user,
    "assert_task_for_telegram_user": agent_tools.assert_task_for_telegram_user,
    "complete_task": agent_tools.complete_task,
    "submit_task_proof": agent_tools.submit_task_proof,
    "approve_task": agent_tools.approve_task,
    "reject_task_proof": agent_tools.reject_task_proof,
    "postpone_task": agent_tools.postpone_task,
    "create_task": agent_tools.create_task,
    "create_event": agent_tools.create_event,
    "get_events_for_dates": agent_tools.get_events_for_dates,
    "get_today_events": agent_tools.get_today_events,
    "save_automation": agent_tools.save_automation,
    "read_drive_document": agent_tools.read_drive_document,
    "search_knowledge": agent_tools.search_knowledge,
    "sync_knowledge_folder": agent_tools.sync_knowledge_folder,
    "list_knowledge_sources": agent_tools.list_knowledge_sources,
    "register_employee": agent_tools.register_employee,
    "register_employees_bulk": agent_tools.register_employees_bulk,
    "delegate_private_reminder": agent_tools.delegate_private_reminder,
    "send_dm_to_employee": agent_tools.send_dm_to_employee,
    "send_brief_to_primary_work_chat": agent_tools.send_brief_to_primary_work_chat,
    "get_employee_directory": agent_tools.get_employee_directory,
    "save_fact": agent_tools.save_fact,
}


def _get_client() -> genai.Client:
    global _client
    if _client is None:
        _client = genai.Client(api_key=settings.gemini_api_key)
    return _client


def _is_gemini_quota_exhausted(exc: BaseException) -> bool:
    if not isinstance(exc, genai_errors.APIError) or exc.code != 429:
        return False
    text = str(exc).lower()
    return (
        "quota" in text
        or "free_tier" in text
        or "resource_exhausted" in text
        or "exceeded your current quota" in text
    )


def _is_retryable_gemini_error(exc: BaseException) -> bool:
    if _is_gemini_quota_exhausted(exc):
        return False
    return (
        isinstance(exc, genai_errors.APIError)
        and exc.code in GEMINI_RETRYABLE_STATUS_CODES
    )


def _user_message_for_agent_error(exc: BaseException) -> str:
    if use_openai_llm():
        try:
            from openai import APIStatusError, RateLimitError

            if isinstance(exc, RateLimitError):
                return OVERLOAD_MESSAGE
            if isinstance(exc, APIStatusError) and exc.status_code in {429, 503}:
                return OVERLOAD_MESSAGE
        except ImportError:
            pass
    if _is_gemini_quota_exhausted(exc):
        return QUOTA_EXHAUSTED_MESSAGE
    if isinstance(exc, genai_errors.APIError) and exc.code == 503:
        return OVERLOAD_MESSAGE
    if isinstance(exc, genai_errors.APIError) and exc.code == 429:
        return OVERLOAD_MESSAGE
    return ERROR_MESSAGE


async def _generate_content_with_retry(
    *,
    model: str,
    contents: list[types.Content],
    config: types.GenerateContentConfig | None = None,
) -> Any:
    client = _get_client()
    max_attempts = len(GEMINI_RETRY_DELAYS_SEC) + 1
    last_error: BaseException | None = None

    for attempt in range(max_attempts):
        try:
            return await client.aio.models.generate_content(
                model=model,
                contents=contents,
                config=config,
            )
        except Exception as exc:
            last_error = exc
            if _is_retryable_gemini_error(exc) and attempt < max_attempts - 1:
                delay = GEMINI_RETRY_DELAYS_SEC[attempt]
                logger.warning(
                    "Gemini API %s, retry %s/%s in %ss",
                    getattr(exc, "code", "?"),
                    attempt + 1,
                    max_attempts - 1,
                    delay,
                )
                await asyncio.sleep(delay)
                continue
            raise

    assert last_error is not None
    raise last_error


def _now_local_str() -> str:
    return datetime.now(ZoneInfo(settings.timezone)).strftime("%Y-%m-%d %H:%M:%S")


def _normalize_role(role: str) -> str:
    value = (role or "user").strip().lower()
    if value in {"assistant", "bot", "model"}:
        return "model"
    return "user"


async def _load_cross_chat_system_section(chat_id: int) -> str:
    if not settings.memory_cross_chat_enabled:
        return ""
    limit = int(settings.memory_cross_chat_limit)
    if limit < 1:
        return ""

    try:
        other_rows = await sheets.get_recent_history_other_chats(chat_id, limit=limit)
        if not other_rows:
            return ""
        labels = await sheets.get_chat_labels_map()
        return build_cross_chat_system_section(
            other_rows,
            labels,
            max_content_chars=int(settings.memory_cross_chat_max_chars),
        )
    except Exception as exc:
        logger.warning(
            "cross-chat memory skipped: chat_id=%s error=%s",
            chat_id,
            exc,
            exc_info=True,
        )
        return ""


GROUP_CHAT_MODE_SECTION = (
    "## Режим группового чата\n"
    "Сейчас ответ уходит в общий чат. Максимум 1–2 коротких предложения. "
    "Не повторяй одно и то же. Не пиши длинные пояснения без @упоминания бота."
)


def _build_system_instruction(
    facts: list[dict[str, Any]],
    cross_chat_section: str = "",
    staff_section: str = "",
    *,
    group_chat_mode: bool = False,
) -> str:
    parts = [SYSTEM_PROMPT]

    if group_chat_mode:
        parts.append(GROUP_CHAT_MODE_SECTION)

    if staff_section.strip():
        parts.append(staff_section.strip())

    if cross_chat_section.strip():
        parts.append(cross_chat_section.strip())

    if facts:
        lines = [
            f"- {row.get('fact', '')}"
            for row in facts
            if str(row.get("fact", "")).strip()
        ]
        if lines:
            parts.append("## Сохранённые факты\n" + "\n".join(lines))

    return "\n\n".join(parts)


def _build_contents(
    history_messages: list[dict[str, Any]],
    user_message: str,
) -> list[types.Content]:
    contents: list[types.Content] = []

    for message in history_messages:
        role = _normalize_role(str(message.get("role", "user")))
        text = str(message.get("content", "")).strip()
        if not text:
            continue
        contents.append(types.Content(role=role, parts=[types.Part(text=text)]))

    contents.append(
        types.Content(role="user", parts=[types.Part(text=user_message.strip())])
    )
    return contents


def _to_jsonable(value: Any) -> Any:
    try:
        json.dumps(value)
        return value
    except TypeError:
        return str(value)


async def _execute_tool(name: str, args: dict[str, Any]) -> dict[str, Any]:
    tool_fn = TOOL_REGISTRY.get(name)
    if tool_fn is None:
        return {"error": f"Unknown tool: {name}"}

    try:
        logger.info("Gemini tool call: %s args=%s", name, args)
        result = await tool_fn(**args)
        return {"result": _to_jsonable(result)}
    except Exception as exc:
        logger.error("Tool %s failed: %s", name, exc, exc_info=True)
        return {"error": str(exc)}


def _extract_function_calls(
    content: types.Content | None,
) -> list[types.FunctionCall]:
    if content is None or not content.parts:
        return []

    calls: list[types.FunctionCall] = []
    for part in content.parts:
        if part.function_call is not None:
            calls.append(part.function_call)
    return calls


async def _generate_with_tools(
    contents: list[types.Content],
    system_instruction: str,
) -> str:
    tools = types.Tool(function_declarations=FUNCTION_DECLARATIONS)
    config = types.GenerateContentConfig(
        tools=[tools],
        system_instruction=system_instruction,
    )

    conversation = list(contents)
    final_text = ""

    for _ in range(MAX_TOOL_ROUNDS):
        response = await _generate_content_with_retry(
            model=MODEL_ID,
            contents=conversation,
            config=config,
        )

        if not response.candidates:
            final_text = response.text or ERROR_MESSAGE
            break

        candidate = response.candidates[0]
        model_content = candidate.content
        function_calls = _extract_function_calls(model_content)

        if not function_calls:
            final_text = (response.text or "").strip()
            break

        if model_content is not None:
            conversation.append(model_content)

        response_parts: list[types.Part] = []
        for function_call in function_calls:
            tool_name = function_call.name or ""
            tool_args = dict(function_call.args or {})
            tool_result = await _execute_tool(tool_name, tool_args)
            part = types.Part(
                function_response=types.FunctionResponse(
                    name=tool_name,
                    response=tool_result,
                    id=function_call.id,
                )
            )
            response_parts.append(part)

        conversation.append(types.Content(role="user", parts=response_parts))
    else:
        final_text = (response.text or "").strip()

    return format_bot_reply(final_text or ERROR_MESSAGE)


async def _save_history(chat_id: int, user_message: str, model_message: str) -> None:
    timestamp = _now_local_str()
    chat_key = str(chat_id)

    await sheets.append_row(
        "memory_history",
        {
            "chat_id": chat_key,
            "role": "user",
            "content": user_message,
            "timestamp": timestamp,
        },
    )
    await sheets.append_row(
        "memory_history",
        {
            "chat_id": chat_key,
            "role": "model",
            "content": model_message,
            "timestamp": timestamp,
        },
    )


async def evaluate_checklist_proof_text(
    proof_text: str,
    checklist: list[str],
) -> str:
    """Evaluate proof text against checklist (OpenAI or Gemini)."""
    if use_openai_llm():
        from src.agent import openai_llm

        try:
            return await openai_llm.evaluate_checklist_proof_text(proof_text, checklist)
        except Exception as exc:
            logger.error("evaluate_checklist_proof_text failed: %s", exc, exc_info=True)
            raise
    try:
        if not checklist:
            return "[]"
        prompt = build_checklist_vision_prompt(checklist, proof_text)
        response = await _generate_content_with_retry(
            model=MODEL_ID,
            contents=[types.Content(role="user", parts=[types.Part(text=prompt)])],
        )
        return (response.text or "").strip() or "[]"
    except Exception as exc:
        logger.error("evaluate_checklist_proof_text failed: %s", exc, exc_info=True)
        raise


async def describe_photo(image_base64: str, mime_type: str = "image/jpeg") -> str:
    """Analyze photo with vision model. image_base64 — base64-encoded image bytes."""
    if use_openai_llm():
        from src.agent import openai_llm

        try:
            return await openai_llm.describe_photo(image_base64, mime_type=mime_type)
        except Exception as exc:
            logger.error("describe_photo failed: %s", exc, exc_info=True)
            raise
    try:
        image_bytes = base64.b64decode(image_base64, validate=True)
        response = await _generate_content_with_retry(
            model=MODEL_ID,
            contents=[
                types.Content(
                    role="user",
                    parts=[
                        types.Part(text=PHOTO_VISION_PROMPT),
                        types.Part.from_bytes(
                            data=image_bytes,
                            mime_type=mime_type,
                        ),
                    ],
                )
            ],
        )
        return (response.text or "").strip() or "Не удалось распознать содержимое фото."
    except Exception as exc:
        logger.error("describe_photo failed: %s", exc, exc_info=True)
        raise


async def call_agent(
    user_message: str,
    chat_id: int,
    history: list[dict],
    *,
    group_chat_mode: bool = False,
) -> str:
    """Call LLM agent with memory, tools and persist dialogue to memory_history."""
    if use_openai_llm():
        from src.agent import openai_llm

        def _build(facts, cross, staff):
            return _build_system_instruction(
                facts,
                cross,
                staff,
                group_chat_mode=group_chat_mode,
            )

        try:
            return format_bot_reply(
                await openai_llm.run_agent(
                    user_message,
                    chat_id,
                    history,
                    build_system_instruction=_build,
                    load_cross_chat_section=_load_cross_chat_system_section,
                    save_history=_save_history,
                )
            )
        except Exception as exc:
            logger.error(
                "call_agent failed: chat_id=%s error=%s",
                chat_id,
                exc,
                exc_info=True,
            )
            return format_bot_reply(_user_message_for_agent_error(exc))

    try:
        facts = await sheets.get_facts()
        try:
            sheet_history = await sheets.get_recent_history(chat_id, limit=30)
        except Exception as exc:
            logger.warning(
                "call_agent: history unavailable chat_id=%s error=%s",
                chat_id,
                exc,
            )
            sheet_history = []

        merged_history = sheet_history
        if len(history) > len(sheet_history):
            merged_history = history

        history_messages = list(reversed(merged_history))
        cross_chat_section = await _load_cross_chat_system_section(chat_id)
        staff_section = await agent_tools.build_staff_roles_section()
        system_instruction = _build_system_instruction(
            facts,
            cross_chat_section,
            staff_section,
            group_chat_mode=group_chat_mode,
        )
        contents = _build_contents(history_messages, user_message)

        reply = await _generate_with_tools(contents, system_instruction)
        try:
            await _save_history(chat_id, user_message, reply)
        except Exception as exc:
            logger.warning(
                "memory_history save failed (reply still sent): chat_id=%s error=%s",
                chat_id,
                exc,
                exc_info=True,
            )
        return format_bot_reply(reply)
    except Exception as exc:
        logger.error(
            "call_agent failed: chat_id=%s error=%s",
            chat_id,
            exc,
            exc_info=True,
        )
        return format_bot_reply(_user_message_for_agent_error(exc))
