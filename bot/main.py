import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.types import BotCommand, BotCommandScopeChat

from . import session, vault
from .config import OWNER_TELEGRAM_ID, TELEGRAM_BOT_TOKEN
from .handlers import router
from .scheduler import start_scheduler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
log = logging.getLogger("psycho.main")


# Команды, которые видны при наборе «/» в Telegram.
# Описание ≤ 256 символов; ставим коротко и понятно.
BOT_COMMANDS = [
    BotCommand(command="ask", description="Задать вопрос (выбор домена кнопками)"),
    BotCommand(command="requestion", description="Свой вопрос: /requestion <текст>"),
    BotCommand(command="discuss", description="Оппонировать: /discuss [слаг|домен]"),
    BotCommand(command="review", description="Поговорить о своей базе знаний"),
    BotCommand(command="history", description="Все вопросы и ответы"),
    BotCommand(command="retry", description="Задать заново вопрос: /retry N"),
    BotCommand(command="answer", description="Ответить на старый вопрос: /answer N текст"),
    BotCommand(command="ping", description="Проверка живости бота и LLM"),
    BotCommand(command="end", description="Закрыть текущую сессию"),
    BotCommand(command="start", description="Подсказка по командам"),
]


async def _setup_commands(bot: Bot) -> None:
    """Регистрируем команды только для владельца — чтобы никто посторонний даже подсказок не видел."""
    try:
        await bot.set_my_commands(
            commands=BOT_COMMANDS,
            scope=BotCommandScopeChat(chat_id=OWNER_TELEGRAM_ID),
        )
        # Для всех остальных явно сбрасываем — пусто.
        await bot.delete_my_commands()
        log.info("bot commands registered for owner_id=%s", OWNER_TELEGRAM_ID)
    except Exception:
        log.exception("failed to set bot commands")


async def main() -> None:
    vault.ensure_layout()
    restored = session.restore()
    if restored is not None:
        log.info(
            "active session loaded from disk: mode=%s q=%s — продолжим оттуда",
            restored.mode,
            restored.current_q_num,
        )

    bot = Bot(token=TELEGRAM_BOT_TOKEN)
    dp = Dispatcher()
    dp.include_router(router)

    await _setup_commands(bot)
    scheduler = start_scheduler(bot)
    log.info("bot starting polling…")
    try:
        await dp.start_polling(bot)
    finally:
        scheduler.shutdown(wait=False)
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
