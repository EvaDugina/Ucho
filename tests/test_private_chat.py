"""Личный чат — обязательная граница для live и offline updates."""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from aiogram.enums import ChatType
from aiogram.types import CallbackQuery, Chat, Message, Update, User

from bot import recovery, users
from bot.middleware import AccessMiddleware


def _message(uid: int, chat_id: int, chat_type: ChatType, message_id: int) -> Message:
    return Message(
        message_id=message_id,
        date=datetime.now(timezone.utc),
        chat=Chat(id=chat_id, type=chat_type),
        from_user=User(id=uid, is_bot=False, first_name="Тест"),
        text="Привет",
    )


@pytest.mark.asyncio
async def test_middleware_accepts_only_sender_private_chat(monkeypatch):
    uid = 42
    monkeypatch.setattr(users, "is_allowed", lambda value: value == uid)
    monkeypatch.setattr(users, "is_owner", lambda value: value == uid)
    handler = AsyncMock()
    middleware = AccessMiddleware()
    user = User(id=uid, is_bot=False, first_name="Тест")

    private = _message(uid, uid, ChatType.PRIVATE, 1)
    await middleware(handler, private, {"event_from_user": user})
    handler.assert_awaited_once()
    handler.reset_mock()

    for chat_type, chat_id in (
        (ChatType.GROUP, -100),
        (ChatType.SUPERGROUP, -101),
        (ChatType.CHANNEL, -102),
        (ChatType.PRIVATE, uid + 1),
    ):
        message = _message(uid, chat_id, chat_type, 2)
        await middleware(handler, message, {"event_from_user": user})
    handler.assert_not_awaited()


@pytest.mark.asyncio
async def test_middleware_blocks_group_and_inline_callbacks(monkeypatch):
    uid = 42
    monkeypatch.setattr(users, "is_allowed", lambda value: value == uid)
    monkeypatch.setattr(users, "is_owner", lambda value: value == uid)
    handler = AsyncMock()
    middleware = AccessMiddleware()
    user = User(id=uid, is_bot=False, first_name="Тест")

    for message in (
        _message(uid, -100, ChatType.GROUP, 1),
        _message(uid, -101, ChatType.SUPERGROUP, 2),
    ):
        callback = CallbackQuery(
            id="group", from_user=user, chat_instance="test", message=message, data="ask"
        )
        await middleware(handler, callback, {"event_from_user": user})
    inline = CallbackQuery(
        id="inline", from_user=user, chat_instance="test", inline_message_id="inline", data="ask"
    )
    await middleware(handler, inline, {"event_from_user": user})
    handler.assert_not_awaited()

    private = CallbackQuery(
        id="private",
        from_user=user,
        chat_instance="test",
        message=_message(uid, uid, ChatType.PRIVATE, 3),
        data="ask",
    )
    await middleware(handler, private, {"event_from_user": user})
    handler.assert_awaited_once()


@pytest.mark.asyncio
async def test_offline_backlog_processes_only_private_chat(monkeypatch):
    uid = 42
    monkeypatch.setattr(users, "is_allowed", lambda value: value == uid)
    process_user = AsyncMock()
    monkeypatch.setattr(recovery, "_process_offline_user", process_user)
    updates = [
        Update(update_id=1, message=_message(uid, -100, ChatType.GROUP, 1)),
        Update(update_id=2, message=_message(uid, uid, ChatType.PRIVATE, 2)),
    ]
    bot = SimpleNamespace(get_updates=AsyncMock(side_effect=[updates, []]))
    dispatcher = SimpleNamespace(feed_update=AsyncMock())

    await recovery.process_offline_backlog(bot, dispatcher)

    process_user.assert_awaited_once()
    assert process_user.await_args.args[1] == uid
    assert [message.message_id for message in process_user.await_args.args[2]] == [2]
    dispatcher.feed_update.assert_awaited_once_with(bot, updates[0])


@pytest.mark.asyncio
async def test_direct_offline_processing_rejects_group(monkeypatch):
    monkeypatch.setattr(users, "is_allowed", lambda value: True)
    ensure_layout = Mock()
    monkeypatch.setattr(recovery.vault, "ensure_layout", ensure_layout)

    await recovery._process_offline_user(
        SimpleNamespace(), 42, [_message(42, -100, ChatType.GROUP, 1)]
    )

    ensure_layout.assert_not_called()
