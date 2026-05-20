import logging

from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from .config import DAILY_HOUR
from .handlers import send_daily_question

log = logging.getLogger(__name__)


def start_scheduler(bot: Bot) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.add_job(
        send_daily_question,
        trigger=CronTrigger(hour=DAILY_HOUR, minute=0),
        args=[bot],
        id="daily_question",
        replace_existing=True,
    )
    scheduler.start()
    log.info("scheduler started: daily_question at %02d:00 UTC", DAILY_HOUR)
    return scheduler
