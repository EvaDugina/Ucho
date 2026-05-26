from __future__ import annotations

from datetime import datetime, timezone

import pytest
from aiogram.types import Chat, Message, User

from bot import userctx
from bot.config import OWNER_TELEGRAM_ID
from bot.middleware import AccessMiddleware


def _message(*, uid: int = OWNER_TELEGRAM_ID, text: str | None = None) -> Message:
    return Message(
        message_id=42,
        date=datetime(2026, 5, 26, tzinfo=timezone.utc),
        chat=Chat(id=uid, type="private"),
        from_user=User(id=uid, is_bot=False, first_name="User"),
        text=text,
    )


@pytest.mark.asyncio
async def test_access_middleware_passes_text_messages() -> None:
    msg = _message(text="Привет")
    called = False

    async def handler(event, data):
        nonlocal called
        called = True
        assert event is msg
        assert data["event_from_user"].id == OWNER_TELEGRAM_ID
        return "ok"

    result = await AccessMiddleware()(handler, msg, {"event_from_user": msg.from_user})

    assert result == "ok"
    assert called is True
    assert userctx.current_uid() == OWNER_TELEGRAM_ID


@pytest.mark.asyncio
async def test_access_middleware_replies_to_non_text_messages(monkeypatch) -> None:
    msg = _message(text=None)
    called = False
    replies: list[str] = []

    async def fake_answer(self, text, **kwargs):
        assert self is msg
        assert kwargs == {}
        replies.append(text)

    monkeypatch.setattr(Message, "answer", fake_answer)

    async def handler(event, data):
        nonlocal called
        called = True
        return "ok"

    result = await AccessMiddleware()(handler, msg, {"event_from_user": msg.from_user})

    assert result is None
    assert called is False
    assert len(replies) == 1
    assert "ухо" in replies[0].lower()
    assert "глаз" in replies[0].lower()
    assert userctx.current_uid() == OWNER_TELEGRAM_ID
