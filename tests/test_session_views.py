from __future__ import annotations

import uuid
from datetime import datetime, timezone

from bot import session_log, vault


def test_markdown_view_rebuilds_from_jsonl(as_user):
    session_id = uuid.uuid4().hex
    at = datetime(2026, 9, 19, 12, 34, 56, tzinfo=timezone.utc)
    event = session_log.append_required(
        session_id=session_id,
        role="user",
        kind="answer",
        text="Первая строка\nВторая строка",
        at=at,
    )
    raw = vault.sessions_dir() / f"20260919T123456_{session_id}.jsonl"
    view = raw.with_suffix(".md")
    assert raw.exists()
    assert event["event_id"] == f"{session_id}:000001"
    assert "Первая строка" in view.read_text(encoding="utf-8")

    view.write_text("ручная правка", encoding="utf-8")
    assert session_log.rebuild_views() == 1
    rebuilt = view.read_text(encoding="utf-8")
    assert "Первая строка" in rebuilt
    assert "ручная правка" not in rebuilt


def test_timestamped_sessions_keep_raw_ids_and_accept_new_events(as_user):
    at = datetime(2026, 9, 19, 12, 34, 56, tzinfo=timezone.utc)
    session_ids = [uuid.uuid4().hex, "telegram-import"]
    original_events = []
    for message_id, session_id in enumerate(session_ids, 10):
        event = session_log.append_required(
            session_id=session_id,
            role="user",
            kind="note",
            text="Сохранённый текст",
            at=at,
            message_id=message_id,
        )
        original_events.append(event)

    for message_id, (session_id, first) in enumerate(zip(session_ids, original_events), 10):
        named = vault.sessions_dir() / session_log.timestamped_filename(session_id, at)
        assert named.exists() and named.with_suffix(".md").exists()
        assert session_log.find_session_by_message_id(message_id) == session_id
        second = session_log.append_required(
            session_id=session_id,
            role="assistant",
            kind="reaction",
            text="Ответ",
            at=at,
        )
        assert second["event_id"] == f"{session_id}:000002"
        assert [row["event_id"] for row in session_log.session_events(session_id)] == [
            first["event_id"],
            second["event_id"],
        ]
        assert len(list(vault.sessions_dir().glob(f"*_{session_log.filename_uuid(session_id)}.jsonl"))) == 1


def test_rebuild_skips_broken_jsonl_and_keeps_other_views(as_user):
    directory = vault.sessions_dir()
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "broken.jsonl").write_text("{bad json\n", encoding="utf-8")
    (directory / "valid.jsonl").write_text(
        '{"role":"user","ts":"2026-09-19","kind":"answer","text":"Вопрос"}\n',
        encoding="utf-8",
    )
    assert session_log.rebuild_views() == 1
    assert not (directory / "broken.md").exists()
    assert "Вопрос" in (directory / "valid.md").read_text(encoding="utf-8")


def test_latest_answered_session_uses_latest_user_event_time(as_user):
    older_session = uuid.uuid4().hex
    newer_session = uuid.uuid4().hex
    session_log.append_required(
        session_id=older_session,
        role="assistant",
        kind="question",
        text="Старый вопрос",
        at=datetime(2026, 9, 19, 10, 0, tzinfo=timezone.utc),
    )
    session_log.append_required(
        session_id=newer_session,
        role="assistant",
        kind="question",
        text="Новый вопрос",
        at=datetime(2026, 9, 20, 10, 0, tzinfo=timezone.utc),
    )
    session_log.append_required(
        session_id=newer_session,
        role="user",
        kind="answer",
        text="Более ранний ответ",
        at=datetime(2026, 9, 21, 10, 0, tzinfo=timezone.utc),
    )
    session_log.append_required(
        session_id=older_session,
        role="user",
        kind="answer",
        text="Самый поздний ответ",
        at=datetime(2026, 9, 23, 10, 0, tzinfo=timezone.utc),
    )

    result = session_log.latest_answered_session_transcript()
    assert "Самый поздний ответ" in result
    assert "Более ранний ответ" not in result
