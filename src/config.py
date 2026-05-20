"""Application settings loaded from environment variables."""

from pathlib import Path

from dotenv import load_dotenv
from pydantic import Field, ValidationError, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_ENV_FILE = _PROJECT_ROOT / ".env"

load_dotenv(_ENV_FILE)

_ENV_ALIASES: dict[str, str] = {
    "bot_token": "BOT_TOKEN",
    "gemini_api_key": "GEMINI_API_KEY",
    "llm_provider": "LLM_PROVIDER",
    "openai_api_key": "OPENAI_API_KEY",
    "openai_model": "OPENAI_MODEL",
    "spreadsheet_id": "SPREADSHEET_ID",
    "source_spreadsheet_id": "SOURCE_SPREADSHEET_ID",
    "source_schedule_sheet_name": "SOURCE_SCHEDULE_SHEET_NAME",
    "schedule_role_category_aliases_json": "SCHEDULE_ROLE_CATEGORY_ALIASES_JSON",
    "employee_role_aliases_json": "EMPLOYEE_ROLE_ALIASES_JSON",
    "schedule_unpacking_roles": "SCHEDULE_UNPACKING_ROLES",
    "google_credentials_json": "GOOGLE_CREDENTIALS_JSON",
    "webhook_url": "WEBHOOK_URL",
    "webhook_secret": "WEBHOOK_SECRET",
    "timezone": "TIMEZONE",
    "dev_mode": "DEV_MODE",
    "task_reminder_max_sends": "TASK_REMINDER_MAX_SENDS",
    "task_reminder_escalate_after": "TASK_REMINDER_ESCALATE_AFTER",
    "task_reminder_hours": "TASK_REMINDER_HOURS",
    "task_reminder_window_minutes": "TASK_REMINDER_WINDOW_MINUTES",
    "memory_cross_chat_enabled": "MEMORY_CROSS_CHAT_ENABLED",
    "memory_cross_chat_limit": "MEMORY_CROSS_CHAT_LIMIT",
    "memory_cross_chat_max_chars": "MEMORY_CROSS_CHAT_MAX_CHARS",
    "group_notice_cooldown_sec": "GROUP_NOTICE_COOLDOWN_SEC",
    "group_agent_require_mention": "GROUP_AGENT_REQUIRE_MENTION",
    "drive_knowledge_folder_id": "DRIVE_KNOWLEDGE_FOLDER_ID",
    "knowledge_sync_enabled": "KNOWLEDGE_SYNC_ENABLED",
    "knowledge_sync_hour": "KNOWLEDGE_SYNC_HOUR",
    "knowledge_sync_minute": "KNOWLEDGE_SYNC_MINUTE",
    "knowledge_chunk_max_chars": "KNOWLEDGE_CHUNK_MAX_CHARS",
    "knowledge_search_top_k": "KNOWLEDGE_SEARCH_TOP_K",
    "knowledge_embedding_model": "KNOWLEDGE_EMBEDDING_MODEL",
    "google_tasks_use_oauth": "GOOGLE_TASKS_USE_OAUTH",
    "google_tasks_oauth_client_json": "GOOGLE_TASKS_OAUTH_CLIENT_JSON",
    "google_tasks_oauth_token_path": "GOOGLE_TASKS_OAUTH_TOKEN_PATH",
    "google_tasks_manager_name": "GOOGLE_TASKS_MANAGER_NAME",
    "google_tasks_manager_telegram_id": "GOOGLE_TASKS_MANAGER_TELEGRAM_ID",
    "google_tasks_my_list_title": "GOOGLE_TASKS_MY_LIST_TITLE",
    "google_tasks_reminders_enabled": "GOOGLE_TASKS_REMINDERS_ENABLED",
    "google_tasks_sheets_sync_enabled": "GOOGLE_TASKS_SHEETS_SYNC_ENABLED",
    "calendar_id": "CALENDAR_ID",
    "calendar_primary_id": "CALENDAR_PRIMARY_ID",
    "calendar_events_id": "CALENDAR_EVENTS_ID",
    "calendar_primary_label": "CALENDAR_PRIMARY_LABEL",
    "calendar_events_label": "CALENDAR_EVENTS_LABEL",
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_ENV_FILE if _ENV_FILE.exists() else None,
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    bot_token: str = Field(validation_alias="BOT_TOKEN")
    llm_provider: str = Field(default="openai", validation_alias="LLM_PROVIDER")
    openai_api_key: str = Field(default="", validation_alias="OPENAI_API_KEY")
    openai_model: str = Field(default="gpt-4o-mini", validation_alias="OPENAI_MODEL")
    gemini_api_key: str = Field(default="", validation_alias="GEMINI_API_KEY")
    spreadsheet_id: str = Field(validation_alias="SPREADSHEET_ID")
    source_spreadsheet_id: str = Field(validation_alias="SOURCE_SPREADSHEET_ID")
    source_schedule_sheet_name: str = Field(
        default="График Текущий месяц",
        validation_alias="SOURCE_SCHEDULE_SHEET_NAME",
    )
    schedule_role_category_aliases_json: str = Field(
        default="",
        validation_alias="SCHEDULE_ROLE_CATEGORY_ALIASES_JSON",
    )
    employee_role_aliases_json: str = Field(
        default="",
        validation_alias="EMPLOYEE_ROLE_ALIASES_JSON",
    )
    schedule_unpacking_roles: str = Field(
        default="Kleener",
        validation_alias="SCHEDULE_UNPACKING_ROLES",
    )
    google_credentials_json: str = Field(validation_alias="GOOGLE_CREDENTIALS_JSON")
    webhook_url: str = Field(validation_alias="WEBHOOK_URL")
    webhook_secret: str = Field(validation_alias="WEBHOOK_SECRET")
    timezone: str = Field(validation_alias="TIMEZONE")
    dev_mode: bool = Field(default=True, validation_alias="DEV_MODE")
    task_reminder_max_sends: int = Field(
        default=3,
        ge=1,
        le=50,
        validation_alias="TASK_REMINDER_MAX_SENDS",
    )
    task_reminder_escalate_after: int = Field(
        default=2,
        ge=1,
        le=50,
        validation_alias="TASK_REMINDER_ESCALATE_AFTER",
    )
    task_reminder_hours: str = Field(
        default="10,18",
        validation_alias="TASK_REMINDER_HOURS",
    )
    task_reminder_window_minutes: int = Field(
        default=3,
        ge=1,
        le=15,
        validation_alias="TASK_REMINDER_WINDOW_MINUTES",
    )
    memory_cross_chat_enabled: bool = Field(
        default=True,
        validation_alias="MEMORY_CROSS_CHAT_ENABLED",
    )
    memory_cross_chat_limit: int = Field(
        default=20,
        ge=0,
        le=100,
        validation_alias="MEMORY_CROSS_CHAT_LIMIT",
    )
    memory_cross_chat_max_chars: int = Field(
        default=400,
        ge=80,
        le=2000,
        validation_alias="MEMORY_CROSS_CHAT_MAX_CHARS",
    )
    group_notice_cooldown_sec: int = Field(
        default=3600,
        ge=0,
        le=86400,
        validation_alias="GROUP_NOTICE_COOLDOWN_SEC",
    )
    group_agent_require_mention: bool = Field(
        default=True,
        validation_alias="GROUP_AGENT_REQUIRE_MENTION",
    )
    drive_knowledge_folder_id: str = Field(
        default="",
        validation_alias="DRIVE_KNOWLEDGE_FOLDER_ID",
    )
    knowledge_sync_enabled: bool = Field(
        default=True,
        validation_alias="KNOWLEDGE_SYNC_ENABLED",
    )
    knowledge_sync_hour: int = Field(
        default=8,
        ge=0,
        le=23,
        validation_alias="KNOWLEDGE_SYNC_HOUR",
    )
    knowledge_sync_minute: int = Field(
        default=0,
        ge=0,
        le=59,
        validation_alias="KNOWLEDGE_SYNC_MINUTE",
    )
    knowledge_chunk_max_chars: int = Field(
        default=900,
        ge=200,
        le=4000,
        validation_alias="KNOWLEDGE_CHUNK_MAX_CHARS",
    )
    knowledge_search_top_k: int = Field(
        default=8,
        ge=1,
        le=30,
        validation_alias="KNOWLEDGE_SEARCH_TOP_K",
    )
    knowledge_embedding_model: str = Field(
        default="text-embedding-004",
        validation_alias="KNOWLEDGE_EMBEDDING_MODEL",
    )
    google_tasks_use_oauth: bool = Field(
        default=True,
        validation_alias="GOOGLE_TASKS_USE_OAUTH",
    )
    google_tasks_oauth_client_json: str = Field(
        default="secrets/google_tasks_oauth_client.json",
        validation_alias="GOOGLE_TASKS_OAUTH_CLIENT_JSON",
    )
    google_tasks_oauth_token_path: str = Field(
        default="secrets/google_tasks_token.json",
        validation_alias="GOOGLE_TASKS_OAUTH_TOKEN_PATH",
    )
    google_tasks_manager_name: str = Field(
        default="",
        validation_alias="GOOGLE_TASKS_MANAGER_NAME",
    )
    google_tasks_manager_telegram_id: str = Field(
        default="",
        validation_alias="GOOGLE_TASKS_MANAGER_TELEGRAM_ID",
    )
    google_tasks_my_list_title: str = Field(
        default="Мои задачи",
        validation_alias="GOOGLE_TASKS_MY_LIST_TITLE",
    )
    google_tasks_reminders_enabled: bool = Field(
        default=True,
        validation_alias="GOOGLE_TASKS_REMINDERS_ENABLED",
    )
    google_tasks_sheets_sync_enabled: bool = Field(
        default=True,
        validation_alias="GOOGLE_TASKS_SHEETS_SYNC_ENABLED",
    )
    calendar_id: str = Field(
        default="",
        validation_alias="CALENDAR_ID",
    )
    calendar_primary_id: str = Field(
        default="",
        validation_alias="CALENDAR_PRIMARY_ID",
    )
    calendar_events_id: str = Field(
        default="",
        validation_alias="CALENDAR_EVENTS_ID",
    )
    calendar_primary_label: str = Field(
        default="Основной",
        validation_alias="CALENDAR_PRIMARY_LABEL",
    )
    calendar_events_label: str = Field(
        default="Мероприятия",
        validation_alias="CALENDAR_EVENTS_LABEL",
    )

    @field_validator("llm_provider", mode="before")
    @classmethod
    def _normalize_llm_provider(cls, value: object) -> str:
        if value is None or (isinstance(value, str) and not value.strip()):
            return "openai"
        return str(value).strip().lower()

    @field_validator(
        "bot_token",
        "spreadsheet_id",
        "source_spreadsheet_id",
        "google_credentials_json",
        "webhook_url",
        "webhook_secret",
        "timezone",
        mode="before",
    )
    @classmethod
    def _strip_non_empty(cls, value: object) -> object:
        if value is None:
            return value
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return None
            return stripped
        return value

    @field_validator("source_schedule_sheet_name", mode="before")
    @classmethod
    def _default_source_schedule_sheet(cls, value: object) -> object:
        if value is None or (isinstance(value, str) and not value.strip()):
            return "График Текущий месяц"
        return value

    @field_validator("google_tasks_reminders_enabled", mode="before")
    @classmethod
    def _parse_google_tasks_reminders_enabled(cls, value: object) -> bool:
        if value is None or value == "":
            return True
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"1", "true", "yes", "on"}:
                return True
            if normalized in {"0", "false", "no", "off"}:
                return False
        return bool(value)

    @field_validator("google_tasks_sheets_sync_enabled", mode="before")
    @classmethod
    def _parse_google_tasks_sheets_sync_enabled(cls, value: object) -> bool:
        if value is None or value == "":
            return True
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"1", "true", "yes", "on"}:
                return True
            if normalized in {"0", "false", "no", "off"}:
                return False
        return bool(value)

    @field_validator("google_tasks_use_oauth", mode="before")
    @classmethod
    def _parse_google_tasks_use_oauth(cls, value: object) -> bool:
        if value is None or value == "":
            return True
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"1", "true", "yes", "on"}:
                return True
            if normalized in {"0", "false", "no", "off"}:
                return False
        return bool(value)

    @field_validator("knowledge_sync_enabled", mode="before")
    @classmethod
    def _parse_knowledge_sync_enabled(cls, value: object) -> bool:
        if value is None or value == "":
            return True
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"1", "true", "yes", "on"}:
                return True
            if normalized in {"0", "false", "no", "off"}:
                return False
        return bool(value)

    @field_validator("memory_cross_chat_enabled", mode="before")
    @classmethod
    def _parse_memory_cross_chat_enabled(cls, value: object) -> bool:
        if value is None or value == "":
            return True
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"1", "true", "yes", "on"}:
                return True
            if normalized in {"0", "false", "no", "off"}:
                return False
        return bool(value)

    @field_validator("dev_mode", mode="before")
    @classmethod
    def _parse_dev_mode(cls, value: object) -> bool:
        if value is None or value == "":
            return True
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"1", "true", "yes", "on"}:
                return True
            if normalized in {"0", "false", "no", "off"}:
                return False
            raise ValueError(
                "Переменная окружения DEV_MODE должна быть true/false, 1/0 или yes/no."
            )
        return bool(value)

    @model_validator(mode="after")
    def _validate_llm_keys(self) -> "Settings":
        provider = (self.llm_provider or "openai").strip().lower()
        if provider == "openai" and not self.openai_api_key.strip():
            raise ValueError(
                "Переменная окружения OPENAI_API_KEY не задана. "
                "Добавьте ключ OpenAI в .env (см. .env.example)."
            )
        if provider == "gemini" and not self.gemini_api_key.strip():
            raise ValueError(
                "Переменная окружения GEMINI_API_KEY не задана для LLM_PROVIDER=gemini."
            )
        if self.knowledge_sync_enabled and not self.gemini_api_key.strip():
            raise ValueError(
                "GEMINI_API_KEY нужен для эмбеддингов базы знаний (KNOWLEDGE_SYNC). "
                "Задайте ключ или отключите KNOWLEDGE_SYNC_ENABLED=false."
            )
        return self


def use_openai_llm() -> bool:
    return (settings.llm_provider or "openai").strip().lower() == "openai"


def _validation_error_to_value_error(exc: ValidationError) -> ValueError:
    messages: list[str] = []
    for error in exc.errors():
        loc = error.get("loc", ())
        field_name = str(loc[0]) if loc else "unknown"
        env_name = _ENV_ALIASES.get(field_name, field_name.upper())

        if error.get("type") == "missing":
            messages.append(
                f"Переменная окружения {env_name} не задана. "
                f"Добавьте её в файл .env (см. .env.example)."
            )
        elif field_name == "dev_mode":
            messages.append(str(error.get("msg", error)))
        else:
            messages.append(
                f"Переменная окружения {env_name} задана некорректно: {error.get('msg', error)}"
            )

    return ValueError("\n".join(messages))


def load_settings() -> Settings:
    try:
        return Settings()
    except ValidationError as exc:
        raise _validation_error_to_value_error(exc) from exc


settings = load_settings()
