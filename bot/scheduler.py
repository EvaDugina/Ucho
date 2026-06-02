import logging
from datetime import datetime

from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger

from .config import DAILY_HOUR, DAILY_TZ
from .services.daily_service import daily_targets, send_daily_question
from .services import reminder_service

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


def _schedule_reminder_dispatch(
    scheduler: AsyncIOScheduler,
    bot: Bot,
    run_at: datetime,
) -> None:
    scheduler.add_job(
        _send_due_daily_reminders,
        trigger=DateTrigger(run_date=run_at, timezone=DAILY_TZ),
        args=[bot],
        id="daily_reminder_dispatch",
        replace_existing=True,
    )
    log.info("daily reminder dispatch scheduled at %s", run_at.isoformat(timespec="seconds"))


async def _plan_daily_reminders(bot: Bot, scheduler: AsyncIOScheduler) -> None:
    run_at, targets = await reminder_service.ensure_daily_reminder_plan()
    if run_at is None:
        log.info("daily reminder planning skipped: no unanswered daily targets")
        return
    _schedule_reminder_dispatch(scheduler, bot, run_at)
    log.info("daily reminder targets planned: %s", len(targets))


async def _send_due_daily_reminders(bot: Bot) -> None:
    result = await reminder_service.send_due_daily_reminders(bot)
    log.info(
        "daily reminder dispatch done: sent=%s skipped=%s errors=%s",
        result.get("sent"),
        result.get("skipped"),
        result.get("errors"),
    )


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


async def catch_up_daily_reminders(bot: Bot, scheduler: AsyncIOScheduler) -> None:
    """Restore or create the evening reminder plan after downtime.

    До 01:00 это ещё окно предыдущего daily-дня; позже не бэкфиллим.
    """
    now = reminder_service.local_now()
    planned_at = reminder_service.pending_plan_time(now=now)
    if planned_at is not None:
        if planned_at <= now:
            await _send_due_daily_reminders(bot)
        else:
            _schedule_reminder_dispatch(scheduler, bot, planned_at)
        return
    run_at, targets = await reminder_service.ensure_daily_reminder_plan(now=now)
    if run_at is None:
        return
    if run_at <= now:
        await _send_due_daily_reminders(bot)
    else:
        _schedule_reminder_dispatch(scheduler, bot, run_at)
    log.info("catch_up_daily_reminders planned targets=%s", len(targets))


def start_scheduler(bot: Bot) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone=DAILY_TZ)
    reminder_start = reminder_service.reminder_start_time()
    scheduler.add_job(
        _daily_for_all,
        trigger=CronTrigger(hour=DAILY_HOUR, minute=0, timezone=DAILY_TZ),
        args=[bot],
        id="daily_question",
        replace_existing=True,
    )
    scheduler.add_job(
        _plan_daily_reminders,
        trigger=CronTrigger(
            hour=reminder_start.hour,
            minute=reminder_start.minute,
            timezone=DAILY_TZ,
        ),
        args=[bot, scheduler],
        id="daily_reminder_plan",
        replace_existing=True,
    )
    scheduler.start()
    log.info(
        "scheduler started: daily_question at %02d:00, reminder_plan at %02d:%02d %s",
        DAILY_HOUR,
        reminder_start.hour,
        reminder_start.minute,
        DAILY_TZ,
    )
    return scheduler
