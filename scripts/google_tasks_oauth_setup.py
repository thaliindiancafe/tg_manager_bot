"""One-time OAuth for client's personal Gmail → Google Tasks API."""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.google.oauth_credentials import (
    oauth_client_secrets_path,
    oauth_configured,
    oauth_token_path,
    run_local_oauth_consent,
)


def _configure_stdout() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")


def main() -> None:
    _configure_stdout()
    client = oauth_client_secrets_path()
    token = oauth_token_path()

    print("Google OAuth: Tasks + чтение Calendar (личный Gmail клиента)\n")
    print(f"Client secrets: {client}")
    print(f"Token file:     {token}\n")

    if not client.is_file():
        print("❌ Нет файла OAuth client secrets.\n")
        print("Сделайте в Google Cloud Console (тот же проект, что service account):")
        print("  1. APIs & Services → Enable Google Tasks API и Google Calendar API")
        print("  2. Credentials → Create credentials → OAuth client ID")
        print("  3. Application type: Desktop app")
        print("  4. Скачайте JSON → сохраните как:")
        print(f"     {client}")
        print("\nВ .env:")
        print("  GOOGLE_TASKS_USE_OAUTH=true")
        print(f"  GOOGLE_TASKS_OAUTH_CLIENT_JSON=secrets/google_tasks_oauth_client.json")
        sys.exit(1)

    if oauth_configured():
        print("⚠️ Токен уже есть. Повторный запуск перезапишет его.")
        print("   Войдите в Gmail КЛИЕНТА в открывшемся браузере.\n")

    print(
        "Откроется браузер. Войдите в Gmail ресторана.\n"
        "Разрешите доступ к Tasks и просмотру Calendar.\n"
        "Если токен был только для Tasks — этот запуск обновит права.\n"
    )
    saved = run_local_oauth_consent()
    print(f"✅ Токен сохранён: {saved}")
    print("\nДальше:")
    print("  python scripts/sync_tasklists_to_employees.py")
    print("  python scripts/sync_tasklists_to_employees.py --apply")


if __name__ == "__main__":
    main()
