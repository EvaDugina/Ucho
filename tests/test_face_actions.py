from __future__ import annotations

import json

from bot import face_actions, handlers, moods, userctx


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


def test_face_action_feedback_and_liked_state(as_user):
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
    face_actions.set_message(token, 222, at="2026-05-25T10:01:00")

    rec = face_actions.find_by_message_id(222)
    assert rec is not None
    assert rec["token"] == token
    assert rec["assistant_text"] == "Я здесь."

    assert face_actions.record_mood_feedback(token, "suitable", at="2026-05-25T10:02:00")
    feedback = userctx.user_root() / "_mood_feedback.jsonl"
    assert json.loads(feedback.read_text(encoding="utf-8").splitlines()[0])["verdict"] == "suitable"

    assert face_actions.set_liked(token, liked=None, at="2026-05-25T10:03:00") is True
    state = json.loads((userctx.user_root() / "_liked_replies.json").read_text(encoding="utf-8"))
    liked = state[token]
    assert liked["liked"] is True
    assert liked["assistant_text"] == "Я здесь."
    assert liked["user_text"] == "Мне плохо."
    assert liked["reply_to_user_message_id"] == 111

    assert face_actions.set_liked(token, liked=None, at="2026-05-25T10:04:00") is False
    log_lines = (userctx.user_root() / "_liked_replies_log.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(log_lines) == 2
    assert json.loads(log_lines[-1])["liked"] is False
