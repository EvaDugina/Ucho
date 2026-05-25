from __future__ import annotations

import json

from bot import qmap, questions, session, session_log, sessions, userctx


def test_runtime_indexes_derive_from_session_log(as_user):
    session_log.append(
        session_id="s-derive",
        role="assistant",
        kind="question",
        text="Что тебя держит?",
        at="2026-05-25T10:00:00",
        message_id=10,
        q_num=3,
        domain="identity",
    )
    session_log.append(
        session_id="s-derive",
        role="user",
        kind="answer",
        text="Ответ.",
        at="2026-05-25T10:01:00",
        message_id=11,
        reply_to_message_id=10,
        q_num=3,
        domain="identity",
    )

    entry = qmap.find_by_message_id(10)
    assert entry["q_num"] == 3
    assert entry["text"] == "Что тебя держит?"
    assert questions.recent(1)[0]["n"] == 3
    assert sessions.find_by_message_id(11) == "s-derive"


def test_session_json_keeps_pending_ref_without_history(as_user):
    s = session.start(mode="probe", domain="everyday")
    session.set_question("Вопрос?", "everyday", q_num=1)
    event = session_log.append(
        session_id=s.id,
        role="user",
        kind="answer",
        text="Полный ответ остаётся в 00_raw/sessions.",
        at="2026-05-25T11:00:00",
        message_id=21,
        q_num=1,
        domain="everyday",
    )
    s.pending_answer_event_id = event["event_id"]
    s.record_user("Полный ответ остаётся в 00_raw/sessions.")
    session.persist()

    data = json.loads((userctx.user_root() / "_session.json").read_text(encoding="utf-8"))
    assert data["history"] == []
    assert data["pending_answer"] is None
    assert data["pending_answer_event_id"] == event["event_id"]
    assert session.pending_answer_text(s) == "Полный ответ остаётся в 00_raw/sessions."
