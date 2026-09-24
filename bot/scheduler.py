import logging
from datetime import datetime

from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger

from . import backup
from .config import (
    BACKGROUND_JOBS_ENABLED,
    BACKUP_ENABLED,
    BACKUP_HOUR,
    BACKUP_KEEP,
    BACKUP_PATH,
    BACKUP_WEEKDAY,
    BOOK_REMINDERS_ENABLED,
    DAILY_HOUR,
    DAILY_INTERVAL_MAX_DAYS,
    DAILY_INTERVAL_MIN_DAYS,
    DAILY_TZ,
    VAULT_PATH,
)
from .services import reminder_service
from .services.daily_service import (
    daily_targets,
    send_daily_question,
    send_unanswered_followup_if_due,
)

log = logging.getLogger(__name__)


def _daily_targets() -> list[int]:
    """Кому слать автоматический вопрос: только текущий whitelist."""
    return daily_targets()


async def _daily_for_all(bot: Bot) -> None:
    """Проверить предварительную реплику и срок вопроса для всех доверенных."""
    for uid in _daily_targets():
        try:
            await send_unanswered_followup_if_due(bot, uid)
        except Exception:
            log.exception("unanswered followup failed for uid=%s", uid)
        try:
            await send_daily_question(bot, uid)
        except Exception:
            log.exception("daily question failed for uid=%s", uid)


def _schedule_reminder_dispatch(
    scheduler: AsyncIOScheduler,
    bot: Bot,
    run_at: datetime,
) -> None:
    if not BOOK_REMINDERS_ENABLED:
        return
    scheduler.add_job(
        _send_due_daily_reminders,
        trigger=DateTrigger(run_date=run_at, timezone=DAILY_TZ),
        args=[bot],
        id="daily_reminder_dispatch",
        replace_existing=True,
    )
    log.info("daily reminder dispatch scheduled at %s", run_at.isoformat(timespec="seconds"))


async def _plan_daily_reminders(bot: Bot, scheduler: AsyncIOScheduler) -> None:
    if not BOOK_REMINDERS_ENABLED:
        return
    run_at, targets = await reminder_service.ensure_daily_reminder_plan()
    if run_at is None:
        log.info("daily reminder planning skipped: no unanswered daily targets")
        return
    _schedule_reminder_dispatch(scheduler, bot, run_at)
    log.info("daily reminder targets planned: %s", len(targets))


async def _send_due_daily_reminders(bot: Bot) -> None:
    if not BOOK_REMINDERS_ENABLED:
        return
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
    """Догон после простоя: проверить сохранённые даты реплики и вопроса.

    За пропущенные даты вопросы не бэкфиллим: при наступившем сроке отправляется
    ровно один текущий вопрос, а новая дата становится началом следующего интервала.
    """
    if _now_hour_local() < DAILY_HOUR:
        return  # время рассылки сегодня ещё не наступило — ждём cron
    log.info("catch_up_daily: время рассылки прошло, проверяю интервал вопроса")
    await _daily_for_all(bot)


async def catch_up_daily_reminders(bot: Bot, scheduler: AsyncIOScheduler) -> None:
    """Restore or create the evening reminder plan after downtime.

    До 01:00 это ещё окно предыдущего daily-дня; позже не бэкфиллим.
    """
    if not BOOK_REMINDERS_ENABLED:
        log.info("book reminders disabled by config")
        return
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
    if BACKGROUND_JOBS_ENABLED:
        scheduler.add_job(
            _daily_for_all,
            trigger=CronTrigger(hour=DAILY_HOUR, minute=0, timezone=DAILY_TZ),
            args=[bot],
            id="daily_question",
            replace_existing=True,
        )
        if BOOK_REMINDERS_ENABLED:
            reminder_start = reminder_service.reminder_start_time()
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
        else:
            log.info("book reminders disabled by config")
        log.info(
            "scheduled question every %s-%s days checked at %02d:00 %s",
            DAILY_INTERVAL_MIN_DAYS,
            DAILY_INTERVAL_MAX_DAYS,
            DAILY_HOUR,
            DAILY_TZ,
        )
    if BACKUP_ENABLED:
        scheduler.add_job(
            _make_backup,
            trigger=CronTrigger(
                day_of_week=BACKUP_WEEKDAY, hour=BACKUP_HOUR, minute=0, timezone=DAILY_TZ
            ),
            id="weekly_backup",
            replace_existing=True,
        )
        log.info("weekly backup at %s %02d:00 %s", BACKUP_WEEKDAY, BACKUP_HOUR, DAILY_TZ)
    scheduler.start()
    return scheduler


async def _make_backup() -> None:
    try:
        if backup.has_backup_this_week(BACKUP_PATH, tz_name=DAILY_TZ):
            return
        result = backup.create_backup(VAULT_PATH, BACKUP_PATH, keep=BACKUP_KEEP)
        log.info("backup complete path=%s files=%s bytes=%s", result.path, result.files, result.bytes)
    except Exception:
        log.exception("weekly backup failed")
