"""Единое меню Telegram-команд для доверенного чата."""

from __future__ import annotations

from aiogram import Bot
from aiogram.types import BotCommand, BotCommandScopeChat

BOT_COMMANDS = [
    BotCommand(command="pebble", description="Бросить камень"),
    BotCommand(command="ucho", description="Свободная заметка: /ucho <текст>"),
    BotCommand(command="ask", description="Задать вопрос: /ask [тема]"),
    BotCommand(command="about", description="Показать внутренний профиль"),
    BotCommand(command="leta", description="Удалить личные данные"),
    BotCommand(command="help", description="Подсказка по командам"),
    BotCommand(command="start", description="Начать работу с ботом"),
]

ADMIN_COMMANDS = [
    BotCommand(command="upload", description="Добавить книгу в общую библиотеку"),
    BotCommand(command="sea", description="Книжные разговоры и настройки"),
    BotCommand(command="adduser", description="Добавить пользователя: /adduser <id>"),
    BotCommand(command="removeuser", description="Убрать пользователя: /removeuser <id>"),
    BotCommand(command="users", description="Список доверенных"),
]


async def set_chat_commands(bot: Bot, chat_id: int, *, owner: bool) -> None:
    commands = BOT_COMMANDS + ADMIN_COMMANDS if owner else BOT_COMMANDS
    await bot.set_my_commands(commands=commands, scope=BotCommandScopeChat(chat_id=chat_id))
