"""APScheduler setup for background jobs."""

from __future__ import annotations

import logging
from zoneinfo import ZoneInfo

from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from src.config import settings
from src.scheduler.automations import run_automations, sync_schedule
from src.scheduler.knowledge_sync import sync_knowledge_from_drive

logger = logging.getLogger(__name__)

_scheduler: AsyncIOScheduler | None = None


def start_scheduler(bot: Bot) -> AsyncIOScheduler:
    """Initialize and start APScheduler: automations + task reminders every 5m, schedule sync daily 07:00."""
    global _scheduler

    if _scheduler is not None and _scheduler.running:
        logger.warning("Scheduler is already running")
        return _scheduler

    tz = ZoneInfo(settings.timezone)
    _scheduler = AsyncIOScheduler(timezone=tz)
    _scheduler.add_job(
        run_automations,
        trigger=IntervalTrigger(minutes=5),
        kwargs={"bot": bot},
        id="run_automations",
        replace_existing=True,
        max_instances=1,
    )
    _scheduler.add_job(
        sync_schedule,
        trigger=CronTrigger(hour=7, minute=0, timezone=tz),
        id="sync_schedule",
        replace_existing=True,
        max_instances=1,
    )
    if settings.knowledge_sync_enabled:
        _scheduler.add_job(
            sync_knowledge_from_drive,
            trigger=CronTrigger(
                hour=settings.knowledge_sync_hour,
                minute=settings.knowledge_sync_minute,
                timezone=tz,
            ),
            id="sync_knowledge_from_drive",
            replace_existing=True,
            max_instances=1,
        )
    _scheduler.start()
    knowledge_cron = (
        f"sync_knowledge_from_drive daily {settings.knowledge_sync_hour:02d}:"
        f"{settings.knowledge_sync_minute:02d}"
        if settings.knowledge_sync_enabled
        else "sync_knowledge_from_drive disabled"
    )
    logger.info(
        "Scheduler started: run_automations every 5 min, sync_schedule 07:00, %s (%s)",
        knowledge_cron,
        settings.timezone,
    )
    return _scheduler


def stop_scheduler() -> None:
    """Stop APScheduler if running."""
    global _scheduler

    if _scheduler is None or not _scheduler.running:
        return

    _scheduler.shutdown(wait=False)
    logger.info("Scheduler stopped")
    _scheduler = None
