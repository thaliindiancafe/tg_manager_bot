"""Main bot router — aggregates all handlers in priority order."""

from aiogram import Router

from src.bot.handlers import (
    chatid_router,
    delegation_callbacks_router,
    photo_router,
    start_router,
    status_router,
    task_import_callbacks_router,
    text_router,
)

main_router = Router(name="main")

# Order matters: specific handlers before generic fallbacks.
main_router.include_router(start_router)   # 1. /start
main_router.include_router(chatid_router)  # 2. /chatid
main_router.include_router(status_router)  # 3. task status phrases
main_router.include_router(delegation_callbacks_router)  # 4. delegation inline buttons
main_router.include_router(task_import_callbacks_router)  # 5. task import confirm/cancel
main_router.include_router(photo_router)   # 5. photos
main_router.include_router(text_router)    # 6. other text messages
