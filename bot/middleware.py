"""aiogram-middleware: гейт доступа и установка request-scoped пользователя.

Вынесено из ``handlers.py`` — это транспортная обвязка, общая для message и
callback. На КАЖДЫЙ update: проверяем whitelist (не в списке → молча роняем),
ставим ``userctx`` (per-user маршрутизация данных), один раз показываем
disclaimer о приватности новым гостям и закрываем активную сессию-обсуждение на
любой команде (кроме сервисных исключений).
"""
from __future__ import annotations

import logging
import random

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from . import ratelimit, session, userctx, users

log = logging.getLogger(__name__)

_CONSENT_TEXT = (
    "Это личный бот-собеседник. Он задаёт вопросы и складывает твои ответы в "
    "психо-философский портрет (граф концептов) в локальной базе владельца — "
    "ничего не уходит в облако. Доступ к твоей базе есть у владельца этого бота. "
    "Продолжая пользоваться, ты соглашаешься. Команды — /help."
)

_NON_TEXT_REPLIES = (
    "Бедное то ухо, которое не имеет глаз.",
    "Ухо без глаза слышит стук, но не видит, откуда кровь.",
    "Ты принёс образ. А ухо, бедное, родилось без глаз.",
    "Бедное ухо без глаз: ему показывают, а оно умеет только слушать.",
    "Ухо без глаз не свидетель. Дай словами.",
    "Я слышу только буквы; без глаз ухо слепнет.",
)


def _non_text_reply() -> str:
    return random.choice(_NON_TEXT_REPLIES)


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
            if event.text is None:
                log.info(
                    "ignored non-text message: uid=%s kind=%s message_id=%s",
                    uid,
                    event.content_type,
                    event.message_id,
                )
                try:
                    await event.answer(_non_text_reply())
                except Exception:
                    log.exception("failed to send non-text reply to %s", uid)
                return
            text = event.text or ""
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
        # Любая команда закрывает активную сессию-обсуждение (снапшот в кольцо —
        # её можно продолжить reply на любое её сообщение). Команды-открыватели
        # (/ask, /echo, /about) затем заведут новую.
        # ИСКЛЮЧЕНИЯ: /pebble — проверка живости; /like, /regen и /remask —
        # действия над reply-сообщением; /leta — сначала только предупреждение;
        # /start — безопасный смыв queued_answer во время busy.
        if isinstance(event, Message) and (event.text or "").startswith("/"):
            cmd = (event.text or "").split(maxsplit=1)[0].split("@", 1)[0].lstrip("/").lower()
            busy = session.has_unfinished_answer() or ratelimit.is_inflight(uid)
            if busy:
                if cmd not in {"start", "echo"}:
                    try:
                        await event.answer(ratelimit.BUSY_MESSAGE)
                    except Exception:
                        log.exception("failed to send busy reply to %s", uid)
                    return
                return await handler(event, data)
            if cmd not in {"pebble", "like", "regen", "remask", "leta"} and session.close():
                log.info("session closed by command for uid=%s", uid)
        return await handler(event, data)
