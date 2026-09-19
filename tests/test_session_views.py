from __future__ import annotations

import uuid
from datetime import datetime, timezone

from bot import session_log, userctx, vault
from scripts import rename_session_files


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


def test_renamed_sessions_keep_raw_ids_and_accept_new_events(as_user):
    at = datetime(2026, 9, 19, 12, 34, 56, tzinfo=timezone.utc)
    session_ids = [uuid.uuid4().hex, "legacy-import"]
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
        named = vault.sessions_dir() / session_log.timestamped_filename(session_id, at)
        named.rename(vault.sessions_dir() / f"{session_id}.jsonl")
        named.with_suffix(".md").rename(vault.sessions_dir() / f"{session_id}.md")

    root = userctx.user_root().parents[1]
    changes, already_named = rename_session_files.plan(root, uid=as_user)
    assert len(changes) == 2
    assert already_named == 0
    rename_session_files.apply(changes)
    assert rename_session_files.plan(root, uid=as_user) == ([], 2)

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
