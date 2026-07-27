"""Гейт whitelist, request-scoped user и privacy-disclaimer."""
from __future__ import annotations

import logging

from aiogram import BaseMiddleware
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

class AccessMiddleware(BaseMiddleware):
    """Гейт доступа + установка request-scoped пользователя.

    На КАЖДЫЙ update (message/callback): берёт user_id, проверяет whitelist
    (не в списке → молча роняем), выставляет userctx (per-user маршрутизация
    данных), один раз показывает disclaimer о приватности новым гостям.
    """

    async def __call__(self, handler, event: TelegramObject, data: dict):
        user = data.get("event_from_user")
        uid = user.id if user is not None else None
        if uid is None or not users.is_allowed(uid):
            return  # не в whitelist — тишина
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
            users.set_consent(uid)
        return await handler(event, data)
