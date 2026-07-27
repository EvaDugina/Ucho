from __future__ import annotations

from types import SimpleNamespace

import pytest

from bot import books, session, session_log
from bot.services import reminder_service

TEXT = "Содержательная книжная строка о совести и выборе. " * 30


@pytest.mark.asyncio
async def test_book_reminder_logs_source_and_sets_pending(as_user, monkeypatch):
    book = books.ingest(TEXT.encode(), "quote.txt", uploader_uid=as_user)
    current = session.start(mode="probe", domain="ethics")
    session.set_question("Дневной вопрос", "ethics", q_num=1)
    session_log.append_required(
        session_id=current.id,
        role="assistant",
        kind="question",
        text="Дневной вопрос",
        q_num=1,
        domain="ethics",
    )
    monkeypatch.setattr(books, "choose_for_reminder", lambda: book)
    monkeypatch.setattr(books, "choose_excerpt", lambda book_id: "Точная цитата из книги.")

    class Bot:
        async def send_message(self, uid, text, **kwargs):
            assert "Точная цитата из книги." in text
            return SimpleNamespace(message_id=88)

    candidate = reminder_service.ReminderCandidate(
        uid=as_user,
        day="2026-07-27",
        q_num=1,
        session_id=current.id,
    )
    assert await reminder_service.send_daily_reminder(Bot(), candidate) is True
    event = session_log.session_events(current.id)[-1]
    assert event["kind"] == "book_reminder"
    assert event["metadata"]["book_id"] == book["id"]
    assert books.pending_reminder()["message_id"] == 88


@pytest.mark.asyncio
async def test_no_books_skips_without_fallback(as_user, monkeypatch):
    monkeypatch.setattr(books, "choose_for_reminder", lambda: None)

    class Bot:
        async def send_message(self, *args, **kwargs):
            raise AssertionError("message must not be sent")

    candidate = reminder_service.ReminderCandidate(as_user, "2026-07-27", 1, "missing")
    assert await reminder_service.send_daily_reminder(Bot(), candidate) is False


def test_pending_expires_on_next_daily(as_user):
    book = books.ingest(TEXT.encode(), "expire.txt", uploader_uid=as_user)
    books.set_pending_reminder(book, "Цитата")
    assert books.pending_reminder()
    books.expire_pending_reminder()
    assert books.pending_reminder() is None
    assert books.score(book["id"]) == 0


def test_reply_precedence_does_not_consume_other_target(as_user):
    from bot.handlers import _reply_targets_pending

    pending = {"message_id": 100}
    other_reply = SimpleNamespace(reply_to_message=SimpleNamespace(message_id=99))
    same_reply = SimpleNamespace(reply_to_message=SimpleNamespace(message_id=100))
    plain = SimpleNamespace(reply_to_message=None)
    assert _reply_targets_pending(other_reply, pending) is False
    assert _reply_targets_pending(same_reply, pending) is True
    assert _reply_targets_pending(plain, pending) is True


@pytest.mark.asyncio
async def test_pending_opens_book_conversation_and_scores_once(as_user):
    from bot.handlers import _open_book_reminder

    book = books.ingest((TEXT + " pending").encode(), "pending.txt", uploader_uid=as_user)
    books.set_pending_reminder(
        book,
        "Дословный фрагмент для разговора.",
        message_id=500,
        raw_event_id="old-session:000002",
    )
    pending = books.pending_reminder()
    await _open_book_reminder(SimpleNamespace(), pending)
    current = session.get()
    assert current.last_domain == "knowledge"
    event = session_log.session_events(current.id)[0]
    assert event["kind"] == "book_question"
    assert event["metadata"]["reminder_event_id"] == "old-session:000002"
    assert books.pending_reminder() is None
    assert books.score(book["id"]) == 1
    await _open_book_reminder(SimpleNamespace(), pending)
    assert books.score(book["id"]) == 1
