"""Юнит-тесты сессии: per-uid лок сериализует конкурентные мутации.

Ответы в probe не проходят single-flight ratelimit, поэтому два быстрых
сообщения одного пользователя могли бы переплестись на await-границах. Лок по
uid (session.lock_for) сериализует критическую секцию — порядок не рвётся и
история не теряется.
"""
from __future__ import annotations

import asyncio
from datetime import datetime

import pytest

from bot import session, userctx


@pytest.mark.asyncio
async def test_lock_for_serializes_session_mutations(as_user):
    uid = userctx.current_uid()
    session.start(mode="probe")
    order: list[str] = []

    async def worker(tag: str) -> None:
        async with session.lock_for(uid):
            order.append(f"{tag}-start")
            await asyncio.sleep(0.01)  # окно, в которое без лока влез бы другой
            session.get().record_user(f"msg-{tag}")
            order.append(f"{tag}-end")

    await asyncio.gather(worker("a"), worker("b"))

    # Лок сериализует: одна корутина полностью завершается до старта другой.
    assert order in (
        ["a-start", "a-end", "b-start", "b-end"],
        ["b-start", "b-end", "a-start", "a-end"],
    )
    # Обе реплики записаны — потери истории нет.
    contents = [h["content"] for h in session.get().history]
    assert "msg-a" in contents and "msg-b" in contents


def test_lock_for_is_stable_per_uid(as_user):
    uid = userctx.current_uid()
    assert session.lock_for(uid) is session.lock_for(uid)
    assert session.lock_for(uid) is not session.lock_for(uid + 1)


def test_history_keeps_timestamps_and_marks_last_user_message(as_user):
    s = session.start(mode="probe")
    s.record_assistant("Сначала вопрос.", at=datetime(2026, 5, 23, 19, 10))
    s.record_user("Потом ответ.", at=datetime(2026, 5, 23, 19, 42))

    assert s.history[0]["ts"].startswith("2026-05-23T19:10")
    assert s.history[1]["ts"].startswith("2026-05-23T19:42")

    transcript = s.render_transcript()
    assert "[2026:05:23 19:10] assistant: Сначала вопрос." in transcript
    assert "[2026:05:23 19:42] user [LAST_USER_MESSAGE]: Потом ответ." in transcript


def test_from_dict_migrates_legacy_history_without_ts(as_user):
    s = session.Session.from_dict({
        "mode": "probe",
        "asked_at": "2026-05-23T20:15:00",
        "history": [{"role": "user", "content": "старый ответ"}],
    })

    assert s.history[0]["ts"].startswith("2026-05-23T20:15")
    assert "[2026:05:23 20:15] user [LAST_USER_MESSAGE]: старый ответ" in s.render_transcript()


def test_render_transcript_truncates_older_messages_but_keeps_last_marker(as_user):
    s = session.start(mode="probe")
    for i in range(8):
        s.record_user(f"сообщение-{i} " + ("x" * 40), at=datetime(2026, 5, 23, 12, i))

    transcript = s.render_transcript(max_chars=180)
    assert transcript.startswith("[TRUNCATED_OLDER_SESSION_MESSAGES]")
    assert "user [LAST_USER_MESSAGE]: сообщение-7" in transcript
