import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.types import ErrorEvent

from . import backup, llm, recovery, session, session_log, userctx, users, vault
from .commands import set_chat_commands
from .config import (
    BACKGROUND_JOBS_ENABLED,
    BACKUP_ENABLED,
    BACKUP_KEEP,
    BACKUP_PATH,
    DAILY_TZ,
    LOG_LEVEL,
    OWNER_TELEGRAM_ID,
    STARTUP_RECOVERY_ENABLED,
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_PROXY_URL,
    VAULT_PATH,
)
from .errors import BILLING_MESSAGE
from .handlers import admin_router, router
from .logging_setup import configure_logging
from .middleware import AccessMiddleware
from .scheduler import start_scheduler

configure_logging(LOG_LEVEL)
log = logging.getLogger("ucho.main")


async def _setup_commands(bot: Bot) -> None:
    """Команды видны только доверенным (per-chat scope). Глобально — пусто.
    Владельцу дополнительно показываем админ-команды."""
    try:
        await bot.delete_my_commands()  # для всех остальных — пусто
        for uid in users.allowed_ids():
            try:
                await set_chat_commands(bot, uid, owner=users.is_owner(uid))
            except Exception:
                log.exception("failed to set commands for uid=%s", uid)
        log.info("bot commands registered for %d allowed user(s)", len(users.allowed_ids()))
    except Exception:
        log.exception("failed to set bot commands")


async def _send_billing_alert(bot: Bot) -> None:
    await bot.send_message(OWNER_TELEGRAM_ID, BILLING_MESSAGE)


async def main() -> None:
    # Контекст владельца + структура его данных (на случай свежего вольта).
    userctx.set_user(OWNER_TELEGRAM_ID)
    vault.ensure_layout()
    users_root = VAULT_PATH / "users"
    if users_root.is_dir():
        for user_dir in users_root.iterdir():
            if user_dir.is_dir() and not user_dir.is_symlink() and user_dir.name.isdigit():
                userctx.set_user(int(user_dir.name))
                session_log.rebuild_views()
    userctx.set_user(OWNER_TELEGRAM_ID)
    if BACKUP_ENABLED:
        removed = backup.prune_backups(BACKUP_PATH, keep=BACKUP_KEEP)
        if removed:
            log.info("old backups removed: %s", removed)
        if not backup.has_backup_this_week(BACKUP_PATH, tz_name=DAILY_TZ):
            result = backup.create_backup(VAULT_PATH, BACKUP_PATH, keep=BACKUP_KEEP)
            log.info("startup backup complete path=%s files=%s", result.path, result.files)

    # Восстановление сессий всех пользователей + список pending для recovery.
    restored = session.restore_all()
    pending_uids = [
        uid for uid, s in restored if users.is_allowed(uid) and session.has_pending(s)
    ]
    queued_uids = [
        uid for uid, s in restored if users.is_allowed(uid) and session.has_queued(s)
    ]
    for uid in pending_uids:
        log.info("pending_answer detected for uid=%s — recovery will run after startup", uid)
    for uid in queued_uids:
        log.info("queued_answer detected for uid=%s — recovery will run after pending", uid)

    if TELEGRAM_PROXY_URL:
        log.info("telegram proxy enabled via TELEGRAM_PROXY_URL")
        bot = Bot(token=TELEGRAM_BOT_TOKEN, session=AiohttpSession(proxy=TELEGRAM_PROXY_URL))
    else:
        bot = Bot(token=TELEGRAM_BOT_TOKEN)
    llm.set_billing_notifier(lambda: _send_billing_alert(bot))
    dp = Dispatcher()
    dp.message.middleware(AccessMiddleware())
    dp.callback_query.middleware(AccessMiddleware())
    # admin_router ПЕРВЫМ: его Command-хэндлеры должны матчиться раньше catch-all
    # on_text(F.text) в основном router.
    dp.include_router(admin_router)
    dp.include_router(router)

    @dp.errors()
    async def _on_error(event: ErrorEvent) -> bool:
        # Глобальная сеть безопасности: что не поймал локальный try/except в
        # хэндлере — логируем здесь (трейс в stderr + .ucho/log.md через
        # logging), наружу пользователю трейс НЕ выпускаем. Возвращаем True —
        # помечаем апдейт обработанным, чтобы aiogram не дублировал трейс.
        log.error("unhandled error on update: %r", event.exception, exc_info=event.exception)
        return True

    await _setup_commands(bot)
    scheduler = start_scheduler(bot) if BACKGROUND_JOBS_ENABLED or BACKUP_ENABLED else None
    if not BACKGROUND_JOBS_ENABLED:
        log.info("background jobs disabled by config")

    # Recovery несработавшего LLM-цикла — синхронно (await), ДО склейки офлайн-
    # бэклога: прерванный ответ дожимается и может задать новый вопрос, на который
    # затем лягут офлайн-сообщения. (Раньше был create_task — гонка с поллингом.)
    if STARTUP_RECOVERY_ENABLED:
        for uid in pending_uids:
            try:
                await recovery.process_pending_on_startup(bot, uid)
            except Exception:
                log.exception("pending recovery failed for uid=%s", uid)

        # Durable merge-slot сообщений, пришедших во время прошлой генерации, дожимаем
        # после pending recovery: он не должен обгонять уже взятый в LLM ответ.
        for uid in queued_uids:
            try:
                await recovery.process_queued_on_startup(bot, uid)
            except Exception:
                log.exception("queued recovery failed for uid=%s", uid)

        # Сообщения, пришедшие пока контейнер лежал, — обработать склеенными в один
        # ответ (один итоговый комментарий), ДО старта обычного поллинга.
        try:
            await recovery.process_offline_backlog(bot, dp)
        except Exception:
            log.exception("offline backlog processing failed")
    else:
        log.info("startup recovery disabled by config")

    # Догон дневного вопроса: если бот лежал в час рассылки — дослать сегодняшний
    # (не за прошлые дни). Дедуп по дате внутри send_daily_question.
    if BACKGROUND_JOBS_ENABLED:
        try:
            from .scheduler import catch_up_daily
            await catch_up_daily(bot)
        except Exception:
            log.exception("catch_up_daily failed")

        try:
            from .scheduler import catch_up_daily_reminders
            await catch_up_daily_reminders(bot, scheduler)
        except Exception:
            log.exception("catch_up_daily_reminders failed")

    log.info("bot starting polling…")
    try:
        await dp.start_polling(bot)
    finally:
        llm.set_billing_notifier(None)
        if scheduler is not None:
            scheduler.shutdown(wait=False)
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
