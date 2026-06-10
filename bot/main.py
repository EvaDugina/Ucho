import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.types import BotCommand, BotCommandScopeChat, ErrorEvent

from . import recovery, selfcheck, session, userctx, users, vault
from .config import LOG_LEVEL, OWNER_TELEGRAM_ID, TELEGRAM_PROXY_URL, TELEGRAM_BOT_TOKEN
from .handlers import admin_router, router
from .logging_setup import configure_logging
from .middleware import AccessMiddleware
from .scheduler import start_scheduler

configure_logging(LOG_LEVEL)
log = logging.getLogger("psycho.main")


# Команды, видимые при наборе «/». Базовый набор — для всех доверенных.
BOT_COMMANDS = [
    BotCommand(command="pebble", description="Бросить камень"),
    BotCommand(command="ucho", description="Свободная заметка: /ucho <текст>"),
    BotCommand(command="echo", description="Свой вопрос: /echo <вопрос>"),
    BotCommand(command="ask", description="Задать вопрос: /ask [тема]"),
    BotCommand(command="requestion", description="Повторить выбранный вопрос N"),
    BotCommand(command="about", description="Каким я тебя вижу"),
    BotCommand(command="history", description="Последние вопросы"),
    BotCommand(command="regen", description="Перегенерировать reply-комментарий"),
    BotCommand(command="like", description="Отметить reply-реплику Иуды"),
    BotCommand(command="remask", description="Выбрать маску reply-реплики"),
    BotCommand(command="cancel", description="Убрать отложенный ответ"),
    BotCommand(command="leta", description="Смыть базу и растворить переписку"),
    BotCommand(command="help", description="Подсказка по командам"),
    BotCommand(command="start", description="Бесполезная как мизинец на отрубленной руке."),
]

# Админ-команды — только владельцу, добавляются к базовому набору в его меню.
ADMIN_COMMANDS = [
    BotCommand(command="adduser", description="Добавить пользователя: /adduser <id>"),
    BotCommand(command="removeuser", description="Убрать пользователя: /removeuser <id>"),
    BotCommand(command="users", description="Список доверенных"),
    BotCommand(command="dailyall", description="Разослать дневной вопрос всем сейчас"),
]


async def _setup_commands(bot: Bot) -> None:
    """Команды видны только доверенным (per-chat scope). Глобально — пусто.
    Владельцу дополнительно показываем админ-команды."""
    try:
        await bot.delete_my_commands()  # для всех остальных — пусто
        for uid in users.allowed_ids():
            cmds = BOT_COMMANDS + ADMIN_COMMANDS if users.is_owner(uid) else BOT_COMMANDS
            try:
                await bot.set_my_commands(
                    commands=cmds,
                    scope=BotCommandScopeChat(chat_id=uid),
                )
            except Exception:
                log.exception("failed to set commands for uid=%s", uid)
        log.info("bot commands registered for %d allowed user(s)", len(users.allowed_ids()))
    except Exception:
        log.exception("failed to set bot commands")


async def main() -> None:
    # Контекст владельца + структура его данных (на случай свежего вольта).
    userctx.set_user(OWNER_TELEGRAM_ID)
    vault.ensure_layout()

    # Механический self-check по всем пользователям (без LLM). Не валит старт.
    try:
        summary = selfcheck.run()
        log.info("startup self-check: %s", summary)
    except Exception:
        log.exception("startup self-check failed (non-fatal)")

    # Восстановление сессий всех пользователей + список pending для recovery.
    restored = session.restore_all()
    pending_uids = [uid for uid, s in restored if session.has_pending(s)]
    queued_uids = [uid for uid, s in restored if session.has_queued(s)]
    for uid in pending_uids:
        log.info("pending_answer detected for uid=%s — recovery will run after startup", uid)
    for uid in queued_uids:
        log.info("queued_answer detected for uid=%s — recovery will run after pending", uid)

    if TELEGRAM_PROXY_URL:
        log.info("telegram proxy enabled via TELEGRAM_PROXY_URL")
        bot = Bot(token=TELEGRAM_BOT_TOKEN, session=AiohttpSession(proxy=TELEGRAM_PROXY_URL))
    else:
        bot = Bot(token=TELEGRAM_BOT_TOKEN)
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
        # хэндлере — логируем здесь (трейс в stderr + .psycho/log.md через
        # logging), наружу пользователю трейс НЕ выпускаем. Возвращаем True —
        # помечаем апдейт обработанным, чтобы aiogram не дублировал трейс.
        log.error("unhandled error on update: %r", event.exception, exc_info=event.exception)
        return True

    await _setup_commands(bot)
    scheduler = start_scheduler(bot)

    # Recovery несработавшего LLM-цикла — синхронно (await), ДО склейки офлайн-
    # бэклога: прерванный ответ дожимается и может задать новый вопрос, на который
    # затем лягут офлайн-сообщения. (Раньше был create_task — гонка с поллингом.)
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

    # Догон дневного вопроса: если бот лежал в час рассылки — дослать сегодняшний
    # (не за прошлые дни). Дедуп по дате внутри send_daily_question.
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
        scheduler.shutdown(wait=False)
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
