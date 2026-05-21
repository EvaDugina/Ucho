import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.types import BotCommand, BotCommandScopeChat

from . import handlers, selfcheck, session, vault
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
    BotCommand(command="ask", description="Задать вопрос: /ask [тема]"),
    BotCommand(command="echo", description="Свой вопрос: /echo <вопрос>"),
    BotCommand(command="ucho", description="Свободная заметка: /ucho <текст>"),
    BotCommand(command="review", description="Поговорить о своей базе знаний"),
    BotCommand(command="history", description="История всех вопросов и ответов"),
    BotCommand(command="requestion", description="Повторить вопрос"),
    BotCommand(command="pebble", description="Бросить камушек"),
    BotCommand(command="help", description="Подсказка по командам"),
    BotCommand(command="start", description="Кнопка смыва"),
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

    # Механический self-check при старте (без LLM): MOC rebuild, валидация связей,
    # отчёт о дублях/сиротах в .psycho/startup-check.md. Не должен валить старт.
    try:
        summary = selfcheck.run()
        log.info("startup self-check: %s", summary)
    except Exception:
        log.exception("startup self-check failed (non-fatal)")

    restored = session.restore()
    if restored is not None:
        log.info(
            "active session loaded from disk: mode=%s q=%s — продолжим оттуда",
            restored.mode,
            restored.current_q_num,
        )
        if restored.pending_answer:
            log.info(
                "pending_answer detected (Q%s, %d chars) — recovery will run after startup",
                restored.current_q_num,
                len(restored.pending_answer),
            )

    bot = Bot(token=TELEGRAM_BOT_TOKEN)
    dp = Dispatcher()
    dp.include_router(router)

    await _setup_commands(bot)
    scheduler = start_scheduler(bot)

    # Recovery несработавшего LLM-цикла (двухфазный коммит pending_answer).
    # Запускаем в фоне до start_polling — bot.send_message работает без активного polling,
    # и пользователь увидит уведомление сразу, не дожидаясь следующего входящего сообщения.
    if restored is not None and restored.pending_answer:
        asyncio.create_task(handlers.process_pending_on_startup(bot))

    log.info("bot starting polling…")
    try:
        await dp.start_polling(bot)
    finally:
        scheduler.shutdown(wait=False)
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
