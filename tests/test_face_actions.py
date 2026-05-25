from __future__ import annotations

import json

from bot import face_actions, handlers, moods, session_log, userctx


def test_face_keyboard_contains_faces_feedback_and_like(as_user):
    kb = handlers._face_keyboard("tok123")
    buttons = [b for row in kb.inline_keyboard for b in row]
    texts = [b.text for b in buttons]
    callbacks = [b.callback_data for b in buttons]

    for face in moods.BOT_MOODS:
        assert face in texts
    assert "✓ маска подходит" in texts
    assert "✗ маска не подходит" in texts
    assert "☆ понравилось" in texts
    assert "face:rg:tok123:0" in callbacks
    assert "face:ok:tok123" in callbacks
    assert "face:no:tok123" in callbacks
    assert "face:like:tok123" in callbacks


def test_remask_keyboard_contains_only_face_choices(as_user):
    kb = handlers._remask_keyboard("tok123")
    buttons = [b for row in kb.inline_keyboard for b in row]
    texts = [b.text for b in buttons]
    callbacks = [b.callback_data for b in buttons]

    assert texts == list(moods.BOT_MOODS)
    assert callbacks[0] == "face:rm:tok123:0"
    assert all(cb.startswith("face:rm:tok123:") for cb in callbacks)


def test_face_action_feedback_and_liked_state(as_user):
    question_event = session_log.append(
        session_id="s1",
        role="assistant",
        kind="question",
        text="Что случилось?",
        at="2026-05-25T10:00:00",
        message_id=100,
        q_num=9,
        domain="everyday",
    )
    user_event = session_log.append(
        session_id="s1",
        role="user",
        kind="answer",
        text="Мне плохо.",
        at="2026-05-25T10:00:30",
        message_id=111,
        reply_to_message_id=100,
        q_num=9,
        domain="everyday",
    )
    token = face_actions.create_action(
        session_id="s1",
        q_num=10,
        answered_q_num=9,
        kind="reaction",
        bot_mood="вера",
        assistant_text="Я здесь.",
        user_text="Мне плохо.",
        question="Что случилось?",
        session_context="[2026:05:25 10:00] user: Мне плохо.",
        reply_to_user_message_id=111,
    )
    assistant_event = session_log.append(
        session_id="s1",
        role="assistant",
        kind="reaction",
        text="Я здесь.",
        at="2026-05-25T10:01:00",
        message_id=222,
        reply_to_message_id=111,
        q_num=10,
        domain="everyday",
        bot_mood="вера",
    )
    face_actions.set_message(token, 222, at="2026-05-25T10:01:00")

    rec = face_actions.find_by_message_id(222)
    assert rec is not None
    assert rec["token"] == token
    assert "assistant_text" not in rec
    assert rec["question_event_id"] == question_event["event_id"]
    assert rec["user_event_id"] == user_event["event_id"]
    assert rec["assistant_event_id"] == assistant_event["event_id"]
    assert face_actions.hydrate_action(rec)["assistant_text"] == "Я здесь."

    assert face_actions.record_mood_feedback(token, "suitable", at="2026-05-25T10:02:00")
    feedback = userctx.user_root() / "01_mood" / "feedback.jsonl"
    assert json.loads(feedback.read_text(encoding="utf-8").splitlines()[0])["verdict"] == "suitable"

    assert face_actions.set_liked(token, liked=None, at="2026-05-25T10:03:00") is True
    state = json.loads((userctx.user_root() / "03_personality" / "liked_replies.json").read_text(encoding="utf-8"))
    liked = state[token]
    assert liked["liked"] is True
    assert "assistant_text" not in liked
    assert "user_text" not in liked
    assert liked["assistant_event_id"] == assistant_event["event_id"]
    assert liked["user_event_id"] == user_event["event_id"]
    assert liked["reply_to_user_message_id"] == 111

    assert face_actions.set_liked(token, liked=None, at="2026-05-25T10:04:00") is False
    log_lines = (
        userctx.user_root() / "03_personality" / "liked_replies_log.jsonl"
    ).read_text(encoding="utf-8").splitlines()
    assert len(log_lines) == 2
    assert json.loads(log_lines[-1])["liked"] is False


def test_remask_action_uses_refs_and_updates_bot_mood(as_user):
    event = session_log.append(
        session_id="s2",
        role="assistant",
        kind="question",
        text="Что ты прячешь?",
        at="2026-05-25T12:00:00",
        message_id=333,
        q_num=12,
        domain="identity",
    )

    token = face_actions.create_remask_action(event, at="2026-05-25T12:01:00")
    rec = face_actions.get_action(token)

    assert rec["kind"] == "remask"
    assert rec["assistant_event_id"] == event["event_id"]
    assert rec["message_id"] == 333
    assert "assistant_text" not in rec

    updated = session_log.set_event_bot_mood(event["event_id"], "насмешка")
    assert updated["bot_mood"] == "насмешка"

    found = session_log.find_question_by_message_id(333)
    assert found["bot_mood"] == "насмешка"
    assert found["text"].endswith("лицо Иуды: насмешка")

    assert face_actions.set_bot_mood(token, "насмешка")
    assert face_actions.get_action(token)["bot_mood"] == "насмешка"
