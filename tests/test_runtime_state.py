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


def test_daily_schedule_is_randomized_once_and_persisted(tmp_path, monkeypatch):
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
    monkeypatch.setattr(state_repo.layout, "ensure_layout", lambda: None)
    calls = []

    def choose(lower, upper):
        calls.append((lower, upper))
        return 6

    monkeypatch.setattr(state_repo.random, "randint", choose)

    assert state_repo.daily_question_due("Europe/Moscow", day="2026-09-24") is False
    assert state_repo.unanswered_followup_due("Europe/Moscow", day="2026-09-24") is True
    assert state_repo.daily_question_due("Europe/Moscow", day="2026-09-25") is True
    assert calls == [(4, 7)]
    assert state_repo.daily_schedule("Europe/Moscow") == {
        "last_date": "2026-09-19",
        "interval_days": 6,
        "next_date": "2026-09-25",
        "followup_date": "2026-09-24",
        "q_num": 7,
        "followup_sent": False,
        "followup_sent_at": None,
    }
    assert state_repo.mark_unanswered_followup_sent("Europe/Moscow", q_num=7) is True
    assert state_repo.unanswered_followup_due("Europe/Moscow", day="2026-09-24") is False
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

    assert state_repo.daily_question_due("Europe/Moscow", day="2026-09-23") is True


def test_legacy_daily_without_question_number_does_not_block_schedule(tmp_path, monkeypatch):
    state_file = tmp_path / "_state.json"
    state_file.write_text(json.dumps({"last_daily_date": "2026-09-19"}), encoding="utf-8")
    monkeypatch.setattr(state_repo.layout, "state_file", lambda: state_file)
    monkeypatch.setattr(state_repo.layout, "ensure_layout", lambda: None)
    monkeypatch.setattr(state_repo.random, "randint", lambda lower, upper: 4)

    assert state_repo.daily_question_due("Europe/Moscow", day="2026-09-23") is True
    assert state_repo.unanswered_followup_due("Europe/Moscow", day="2026-09-22") is False


def test_mark_daily_sent_chooses_next_interval(tmp_path, monkeypatch):
    state_file = tmp_path / "_state.json"
    state_file.write_text(json.dumps({"last_q_num": 8}), encoding="utf-8")
    monkeypatch.setattr(state_repo.layout, "state_file", lambda: state_file)
    monkeypatch.setattr(state_repo.layout, "ensure_layout", lambda: None)
    monkeypatch.setattr(state_repo.random, "randint", lambda lower, upper: 7)

    schedule = state_repo.mark_daily_sent_details(
        "Europe/Moscow",
        q_num=8,
        session_id="new-session",
        day="2026-09-24",
    )

    assert schedule["interval_days"] == 7
    assert schedule["followup_date"] == "2026-09-30"
    assert schedule["next_date"] == "2026-10-01"
