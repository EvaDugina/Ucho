"""aiogram-middleware: гейт доступа и установка request-scoped пользователя.

Вынесено из ``handlers.py`` — это транспортная обвязка, общая для message и
callback. На КАЖДЫЙ update: проверяем whitelist (не в списке → молча роняем),
ставим ``userctx`` (per-user маршрутизация данных), один раз показываем
disclaimer о приватности новым гостям и закрываем активную сессию-обсуждение на
любой команде (кроме ``/pebble``).
"""
from __future__ import annotations

import logging

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from . import session, userctx, users

log = logging.getLogger(__name__)

_CONSENT_TEXT = (
    "Это личный бот-собеседник. Он задаёт вопросы и складывает твои ответы в "
    "психо-философский портрет (граф концептов) в локальной базе владельца — "
    "ничего не уходит в облако. Доступ к твоей базе есть у владельца этого бота. "
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
            text = event.text or ""
            kind = "command" if text.startswith("/") else ("text" if text else event.content_type)
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
        # Любая команда закрывает активную сессию-обсуждение (снапшот в кольцо —
        # её можно продолжить reply на любое её сообщение). Команды-открыватели
        # (/ask, /echo, /requestion, /about) затем заведут новую.
        # ИСКЛЮЧЕНИЯ: /pebble — проверка живости; /like — отметка reply
        # на уже отправленную реплику, не должна прерывать обсуждение.
        if isinstance(event, Message) and (event.text or "").startswith("/"):
            cmd = (event.text or "").split(maxsplit=1)[0].split("@", 1)[0].lstrip("/").lower()
            if cmd not in {"pebble", "like"} and session.close():
                log.info("session closed by command for uid=%s", uid)
        return await handler(event, data)
