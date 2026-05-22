import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.types import BotCommand, BotCommandScopeChat

from . import handlers, selfcheck, session, userctx, users, vault
from .config import OWNER_TELEGRAM_ID, TELEGRAM_BOT_TOKEN
from .handlers import AccessMiddleware, router
from .scheduler import start_scheduler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
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
    BotCommand(command="help", description="Подсказка по командам"),
    BotCommand(command="start", description="Кнопка смыва"),
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
    pending_uids = [uid for uid, s in restored if s.pending_answer]
    for uid in pending_uids:
        log.info("pending_answer detected for uid=%s — recovery will run after startup", uid)

    bot = Bot(token=TELEGRAM_BOT_TOKEN)
    dp = Dispatcher()
    dp.message.middleware(AccessMiddleware())
    dp.callback_query.middleware(AccessMiddleware())
    dp.include_router(router)

    await _setup_commands(bot)
    scheduler = start_scheduler(bot)

    # Recovery несработавшего LLM-цикла — синхронно (await), ДО склейки офлайн-
    # бэклога: прерванный ответ дожимается и может задать новый вопрос, на который
    # затем лягут офлайн-сообщения. (Раньше был create_task — гонка с поллингом.)
    for uid in pending_uids:
        try:
            await handlers.process_pending_on_startup(bot, uid)
        except Exception:
            log.exception("pending recovery failed for uid=%s", uid)

    # Сообщения, пришедшие пока контейнер лежал, — обработать склеенными в один
    # ответ (один итоговый комментарий), ДО старта обычного поллинга.
    try:
        await handlers.process_offline_backlog(bot, dp)
    except Exception:
        log.exception("offline backlog processing failed")

    log.info("bot starting polling…")
    try:
        await dp.start_polling(bot)
    finally:
        scheduler.shutdown(wait=False)
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
