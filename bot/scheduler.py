import logging

from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from . import users
from .config import ALLOWED_TELEGRAM_IDS, DAILY_HOUR, OWNER_TELEGRAM_ID
from .handlers import send_daily_question

log = logging.getLogger(__name__)


async def _daily_for_all(bot: Bot) -> None:
    """Дневной вопрос каждому доверенному пользователю (у кого нет активной сессии)."""
    # Владелец + env + рантайм-реестр + те, у кого уже есть папка с данными.
    targets = set(users.allowed_ids()) | set(users.all_data_user_ids())
    targets.add(OWNER_TELEGRAM_ID)
    targets.update(ALLOWED_TELEGRAM_IDS)
    for uid in sorted(targets):
        try:
            await send_daily_question(bot, uid)
        except Exception:
            log.exception("daily question failed for uid=%s", uid)


def start_scheduler(bot: Bot) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.add_job(
        _daily_for_all,
        trigger=CronTrigger(hour=DAILY_HOUR, minute=0),
        args=[bot],
        id="daily_question",
        replace_existing=True,
    )
    scheduler.start()
    log.info("scheduler started: daily_question at %02d:00 UTC", DAILY_HOUR)
    return scheduler
