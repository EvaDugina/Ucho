from __future__ import annotations

from types import SimpleNamespace

import pytest

from bot import books, session, session_log, users
from bot.services import daily_service, reminder_service

TEXT = "# Книга\n\n## Глава\n\n" + "Содержательная книжная строка о совести и выборе. " * 30


def _excerpt(text: str = "Точная цитата из книги.") -> books.BookExcerpt:
    return books.BookExcerpt(
        text=text,
        chapter_id="a" * 16,
        chapter_title="Глава",
        section_path=("Подраздел",),
        source_locator="chapter.xhtml#part",
    )


@pytest.mark.asyncio
async def test_book_reminder_logs_source_and_sets_pending(as_user, monkeypatch):
    monkeypatch.setattr(users, "is_allowed", lambda _: True)
    book = books.ingest(TEXT.encode(), "quote.md", uploader_uid=as_user)
    current = session.start(domain="ethics")
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
    monkeypatch.setattr(books, "choose_excerpt", lambda book_id: _excerpt())

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
    assert event["metadata"]["chapter_id"] == "a" * 16
    assert books.pending_reminder()["message_id"] == 88


@pytest.mark.asyncio
async def test_no_books_skips_without_fallback(as_user, monkeypatch):
    monkeypatch.setattr(users, "is_allowed", lambda _: True)
    monkeypatch.setattr(books, "choose_for_reminder", lambda: None)

    class Bot:
        async def send_message(self, *args, **kwargs):
            raise AssertionError("message must not be sent")

    candidate = reminder_service.ReminderCandidate(as_user, "2026-07-27", 1, "missing")
    assert await reminder_service.send_daily_reminder(Bot(), candidate) is False


@pytest.mark.asyncio
async def test_unusable_selected_book_skips_without_retry_error(as_user, monkeypatch):
    monkeypatch.setattr(users, "is_allowed", lambda _: True)
    book = books.ingest((TEXT + " broken").encode(), "broken.md", uploader_uid=as_user)
    monkeypatch.setattr(books, "choose_for_reminder", lambda: book)

    def fail(_):
        raise books.BookError("broken")

    monkeypatch.setattr(books, "choose_excerpt", fail)

    class Bot:
        async def send_message(self, *args, **kwargs):
            raise AssertionError("message must not be sent")

    candidate = reminder_service.ReminderCandidate(as_user, "2026-07-27", 1, "missing")
    assert await reminder_service.send_daily_reminder(Bot(), candidate) is False


def test_daily_targets_only_include_whitelist(as_user, monkeypatch):
    data_only_uid = as_user + 500_000
    (users.META_DIR.parent / "users" / str(data_only_uid)).mkdir(parents=True)
    monkeypatch.setattr(users, "allowed_ids", lambda: {1, as_user})
    assert daily_service.daily_targets() == [1, as_user]


@pytest.mark.asyncio
async def test_direct_reminder_rechecks_whitelist(as_user, monkeypatch):
    monkeypatch.setattr(users, "is_allowed", lambda _: False)

    class Bot:
        async def send_message(self, *args, **kwargs):
            raise AssertionError("message must not be sent")

    candidate = reminder_service.ReminderCandidate(as_user, "2026-07-27", 1, "session")
    assert await reminder_service.send_daily_reminder(Bot(), candidate) is False


def test_pending_expires_on_next_daily(as_user):
    book = books.ingest(TEXT.encode(), "expire.md", uploader_uid=as_user)
    books.set_pending_reminder(book, _excerpt("Цитата"))
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

    book = books.ingest((TEXT + " pending").encode(), "pending.md", uploader_uid=as_user)
    books.set_pending_reminder(
        book,
        _excerpt("Дословный фрагмент для разговора."),
        message_id=500,
        raw_event_id="old-session:000002",
    )
    await _open_book_reminder()
    current = session.get()
    assert current.last_domain == "knowledge"
    event = session_log.session_events(current.id)[0]
    assert event["kind"] == "book_question"
    assert event["metadata"]["reminder_event_id"] == "old-session:000002"
    assert event["metadata"]["chapter_title"] == "Глава"
    assert books.pending_reminder() is None
    assert books.score(book["id"]) == 1
    await _open_book_reminder()
    assert books.score(book["id"]) == 1


@pytest.mark.asyncio
async def test_busy_pipeline_does_not_consume_pending_reminder(as_user, monkeypatch):
    from bot import handlers

    book = books.ingest((TEXT + " busy").encode(), "busy.md", uploader_uid=as_user)
    books.set_pending_reminder(book, _excerpt("Цитата"), message_id=500)
    monkeypatch.setattr(handlers.ratelimit, "is_inflight", lambda _: True)
    replies = []

    message = SimpleNamespace(
        answer=lambda text: replies.append(text),
    )

    async def answer(text):
        replies.append(text)

    message.answer = answer
    await handlers._process_current_text(
        message,
        "Ответ",
        pending_reminder=books.pending_reminder(),
    )
    assert books.pending_reminder() is not None
    assert books.score(book["id"]) == 0
    assert replies
