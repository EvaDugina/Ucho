"""Гейт whitelist, request-scoped user и privacy-disclaimer."""
from __future__ import annotations

import logging

from aiogram import BaseMiddleware
from aiogram.enums import ChatType
from aiogram.types import CallbackQuery, Message, TelegramObject

from . import userctx, users

log = logging.getLogger(__name__)

_CONSENT_TEXT = (
    "Это личный бот-собеседник. Он хранит дословный журнал твоих разговоров, "
    "производный анализ настроения и черт личности в базе владельца. Книги лежат "
    "в общей библиотеке для всех доверенных пользователей. Текст для анализа "
    "передаётся подключённой LLM. Доступ к базе есть у владельца бота. "
    "Продолжая пользоваться, ты соглашаешься. Команды — /help."
)


def is_private_user_chat(event: Message | CallbackQuery, uid: int) -> bool:
    """Принимать только личный чат самого отправителя, включая callback кнопки."""
    if isinstance(event, Message):
        chat = event.chat
    elif isinstance(event, CallbackQuery):
        chat = event.message.chat if event.message is not None else None
    else:
        return False
    return chat is not None and chat.type == ChatType.PRIVATE and chat.id == uid


class AccessMiddleware(BaseMiddleware):
    """Гейт доступа + установка request-scoped пользователя.

    На КАЖДЫЙ update (message/callback): берёт user_id, проверяет whitelist
    и личный чат отправителя (иначе молча роняем), выставляет userctx
    (per-user маршрутизация данных), один раз показывает disclaimer гостям.
    """

    async def __call__(self, handler, event: TelegramObject, data: dict):
        user = data.get("event_from_user")
        uid = user.id if user is not None else None
        if uid is None or not users.is_allowed(uid) or not is_private_user_chat(event, uid):
            return  # не в whitelist или не личный чат — тишина
        userctx.set_user(uid)
        if isinstance(event, Message):
            if event.text is None and event.document is None:
                log.info(
                    "ignored non-text message: uid=%s kind=%s message_id=%s",
                    uid,
                    event.content_type,
                    event.message_id,
                )
                try:
                    await event.answer("Я принимаю текст и книги через /upload.")
                except Exception:
                    log.exception("failed to send non-text reply to %s", uid)
                return
            text = event.text or event.caption or ""
            kind = "command" if text.startswith("/") else "text"
            log.info(
                "incoming message: uid=%s kind=%s message_id=%s text_len=%s",
                uid,
                kind,
                event.message_id,
                len(text),
            )
        elif isinstance(event, CallbackQuery):
            log.info("incoming callback: uid=%s data=%s", uid, event.data or "")
        # Disclaimer один раз для гостей (не владельца).
        if not users.is_owner(uid) and not users.has_consent(uid):
            try:
                await event.bot.send_message(uid, _CONSENT_TEXT)
            except Exception:
                log.exception("failed to send consent disclaimer to %s", uid)
            else:
                users.set_consent(uid)
        return await handler(event, data)
