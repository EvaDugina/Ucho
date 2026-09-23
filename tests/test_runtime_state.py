from __future__ import annotations

import json

import pytest

from bot import session
from bot.repositories import state_repo


def test_state_rejects_non_object_and_recovers_counter(tmp_path, monkeypatch):
    state_file = tmp_path / "_state.json"
    state_file.write_text(json.dumps(["not", "an", "object"]), encoding="utf-8")
    monkeypatch.setattr(state_repo.layout, "state_file", lambda: state_file)
    monkeypatch.setattr(state_repo.layout, "ensure_layout", lambda: None)
    monkeypatch.setattr(state_repo, "append_log", lambda *args: None)

    assert state_repo.next_q_num() == 1
    saved = json.loads(state_file.read_text(encoding="utf-8"))
    assert saved == {"last_q_num": 1}


def test_state_refuses_symlink(tmp_path, monkeypatch):
    external = tmp_path / "external.json"
    external.write_text('{"last_q_num": 99}', encoding="utf-8")
    state_file = tmp_path / "_state.json"
    state_file.symlink_to(external)
    monkeypatch.setattr(state_repo.layout, "state_file", lambda: state_file)

    assert state_repo._load_state() == {"last_q_num": 0}


def test_restored_session_rejects_unsafe_id():
    with pytest.raises(ValueError, match="unsafe session id"):
        session.Session.from_dict({"id": "../../escape"})


def test_scheduled_question_is_due_every_four_calendar_days(tmp_path, monkeypatch):
    state_file = tmp_path / "_state.json"
    state_file.write_text(
        json.dumps({
            "last_daily_date": "2026-09-19",
            "last_daily_q_num": 7,
            "last_daily_session_id": "old-session",
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(state_repo.layout, "state_file", lambda: state_file)

    assert state_repo.daily_question_due("Europe/Moscow", 4, day="2026-09-22") is False
    assert state_repo.daily_question_due("Europe/Moscow", 4, day="2026-09-23") is True
    assert state_repo.last_daily_record() == {
        "date": "2026-09-19",
        "q_num": 7,
        "session_id": "old-session",
        "sent_at": None,
    }


def test_first_scheduled_question_is_due(tmp_path, monkeypatch):
    state_file = tmp_path / "_state.json"
    state_file.write_text(json.dumps({"last_q_num": 0}), encoding="utf-8")
    monkeypatch.setattr(state_repo.layout, "state_file", lambda: state_file)

    assert state_repo.daily_question_due("Europe/Moscow", 4, day="2026-09-23") is True
