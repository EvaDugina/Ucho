import logging
from datetime import datetime

from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from .config import DAILY_HOUR, DAILY_TZ
from .services.daily_service import daily_targets, send_daily_question

log = logging.getLogger(__name__)


def _daily_targets() -> list[int]:
    """Кому слать дневной вопрос: владелец + env + рантайм-реестр + у кого есть данные."""
    return daily_targets()


async def _daily_for_all(bot: Bot) -> None:
    """Дневной вопрос каждому доверенному. Дедуп по дню — внутри send_daily_question."""
    for uid in _daily_targets():
        try:
            await send_daily_question(bot, uid)
        except Exception:
            log.exception("daily question failed for uid=%s", uid)


def _now_hour_local() -> int:
    """Текущий час в зоне рассылки (DAILY_TZ). Сбой tz → локальный час."""
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo(DAILY_TZ)).hour
    except Exception:
        return datetime.now().hour


async def catch_up_daily(bot: Bot) -> None:
    """Догон после простоя: если бот лежал в час рассылки, дослать СЕГОДНЯШНИЙ
    дневной вопрос (не раньше DAILY_HOUR). За прошлые дни НЕ досылаем — дедуп по
    дате в send_daily_question отправит максимум один сегодняшний; вчерашний маркер
    != сегодня, но мы шлём только «сегодня», поэтому бэкфилла нет.
    """
    if _now_hour_local() < DAILY_HOUR:
        return  # время рассылки сегодня ещё не наступило — ждём cron
    log.info("catch_up_daily: время рассылки прошло, досылаю сегодняшний дневной")
    for uid in _daily_targets():
        try:
            await send_daily_question(bot, uid)
        except Exception:
            log.exception("catch_up_daily failed for uid=%s", uid)


def start_scheduler(bot: Bot) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone=DAILY_TZ)
    scheduler.add_job(
        _daily_for_all,
        trigger=CronTrigger(hour=DAILY_HOUR, minute=0, timezone=DAILY_TZ),
        args=[bot],
        id="daily_question",
        replace_existing=True,
    )
    scheduler.start()
    log.info("scheduler started: daily_question at %02d:00 %s", DAILY_HOUR, DAILY_TZ)
    return scheduler
