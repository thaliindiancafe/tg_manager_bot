"""Application entry point."""

from __future__ import annotations

import asyncio
import logging
import sys
from contextlib import asynccontextmanager
from urllib.parse import urlparse

import uvicorn
from aiogram import Bot, Dispatcher
from aiogram.types import Update
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

from src.bot.router import main_router
from src.agent import tools as agent_tools
from src.config import settings
from src.scheduler.runner import start_scheduler, stop_scheduler
from src.storage import get_store

logger = logging.getLogger(__name__)

bot = Bot(token=settings.bot_token)
agent_tools.configure_tool_bot(bot)
dp = Dispatcher()
dp.include_router(main_router)


def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout,
    )


def _webhook_path() -> str:
    path = urlparse(settings.webhook_url).path
    return path if path else "/webhook"


async def _handle_webhook(request: Request) -> Response:
    secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
    if secret != settings.webhook_secret:
        logger.warning("Webhook rejected: invalid secret token")
        return Response(status_code=403, content="Forbidden")

    try:
        payload = await request.json()
        update = Update.model_validate(payload)
        await dp.feed_update(bot, update)
    except Exception as exc:
        logger.error("Webhook handling failed: %s", exc, exc_info=True)
        return Response(status_code=500, content="Internal Server Error")

    return Response(status_code=200)


@asynccontextmanager
async def _webhook_lifespan(_: Starlette):
    if getattr(settings, "storage_backend", "sheets").strip().lower() == "db":
        store = get_store()
        init = getattr(store, "init", None)
        if callable(init):
            await init()
    start_scheduler(bot)
    await bot.set_webhook(
        url=settings.webhook_url,
        secret_token=settings.webhook_secret,
        drop_pending_updates=False,
    )
    logger.info("Webhook registered: %s", settings.webhook_url)
    try:
        yield
    finally:
        stop_scheduler()
        await bot.delete_webhook(drop_pending_updates=False)
        await bot.session.close()
        logger.info("Bot stopped")


def _create_webhook_app() -> Starlette:
    return Starlette(
        routes=[Route(_webhook_path(), _handle_webhook, methods=["POST"])],
        lifespan=_webhook_lifespan,
    )


async def _run_polling() -> None:
    logger.info("Starting bot in polling mode (DEV_MODE=true)")
    if getattr(settings, "storage_backend", "sheets").strip().lower() == "db":
        store = get_store()
        init = getattr(store, "init", None)
        if callable(init):
            await init()
    start_scheduler(bot)
    try:
        await dp.start_polling(bot)
    finally:
        stop_scheduler()
        await bot.session.close()
        logger.info("Bot stopped")


def main() -> None:
    setup_logging()

    if settings.dev_mode:
        asyncio.run(_run_polling())
        return

    logger.info("Starting bot in webhook mode on port 8080")
    uvicorn.run(
        _create_webhook_app(),
        host="0.0.0.0",
        port=8080,
        log_level="info",
    )


if __name__ == "__main__":
    main()
