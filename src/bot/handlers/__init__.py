from src.bot.handlers.chatid import router as chatid_router
from src.bot.handlers.delegation_callbacks import router as delegation_callbacks_router
from src.bot.handlers.photo import router as photo_router
from src.bot.handlers.start import router as start_router
from src.bot.handlers.status import router as status_router
from src.bot.handlers.text import router as text_router

__all__ = [
    "start_router",
    "chatid_router",
    "status_router",
    "delegation_callbacks_router",
    "text_router",
    "photo_router",
]
