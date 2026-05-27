from __future__ import annotations

import json

from bot import face_actions, handlers, moods, session_log, userctx
from bot.services import session_messages


def test_remask_keyboard_contains_only_face_choices(as_user):
    kb = handlers._remask_keyboard("tok123")
    buttons = [b for row in kb.inline_keyboard for b in row]
    texts = [b.text for b in buttons]
    callbacks = [b.callback_data for b in buttons]

    assert texts == list(moods.BOT_MOODS)
    assert callbacks[0] == "face:rm:tok123:0"
    assert all(cb.startswith("face:rm:tok123:") for cb in callbacks)


def test_mask_postscript_is_italic_for_comments_but_not_questions(as_user):
    rendered = session_messages.with_face_signature("Я здесь.", "сомнение")

    assert rendered == "Я здесь.\n\n<i>Не верю ни единому слову.</i>"
    assert "лицо Иуды" not in rendered
    assert "<i>Не верю" not in session_messages.format_q(1, "probe", "everyday", "Что болит?")


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

    assert face_actions.set_liked(token, liked=True, at="2026-05-25T10:03:00") is True
    assert face_actions.record_user_score(token, 1.0, "favorite", at="2026-05-25T10:03:00")
    feedback = userctx.user_root() / "01_mood" / "feedback.jsonl"
    state = json.loads((userctx.user_root() / "03_personality" / "liked_replies.json").read_text(encoding="utf-8"))
    liked = state[token]
    assert liked["liked"] is True
    assert liked["score"] == 1.0
    assert "assistant_text" not in liked
    assert "user_text" not in liked
    assert liked["assistant_event_id"] == assistant_event["event_id"]
    assert liked["user_event_id"] == user_event["event_id"]
    assert liked["reply_to_user_message_id"] == 111

    log_lines = (
        userctx.user_root() / "03_personality" / "liked_replies_log.jsonl"
    ).read_text(encoding="utf-8").splitlines()
    assert len(log_lines) == 1
    assert json.loads(log_lines[-1])["liked"] is True
    score_lines = feedback.read_text(encoding="utf-8").splitlines()
    assert len(score_lines) == 1
    assert json.loads(score_lines[-1])["score"] == 1.0
    assert json.loads(score_lines[-1])["reason"] == "favorite"


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
    assert found["text"] == "Что ты прячешь?"
    assert "лицо Иуды" not in found["text"]

    assert face_actions.set_bot_mood(token, "насмешка")
    assert face_actions.get_action(token)["bot_mood"] == "насмешка"


def test_opposite_bot_mood_uses_different_cluster(as_user):
    soft_targets = {moods.opposite_bot_mood("насмешка") for _ in range(20)}
    hard_targets = {moods.opposite_bot_mood("ласка") for _ in range(20)}

    assert soft_targets
    assert hard_targets
    assert soft_targets <= {
        "ласка",
        "любовь",
        "вера",
        "вселение_уверенности",
        "смирение",
        "клятва",
        "покорность",
        "жалостливость",
        "боязливость",
        "доброта",
        "милость",
        "забота",
        "бережность",
    }
    assert hard_targets <= {
        "раскачивание",
        "насмешка",
        "подшучивание",
        "давление_на_больное",
        "унижение",
        "перевирание",
        "сомнение",
        "холодная_отстранённость",
    }


def test_regen_chain_excludes_already_generated_faces(as_user):
    session_log.append(
        session_id="s3",
        role="assistant",
        kind="question",
        text="Что случилось?",
        at="2026-05-25T10:00:00",
        message_id=400,
        q_num=15,
        domain="everyday",
    )
    token = face_actions.create_action(
        session_id="s3",
        q_num=16,
        answered_q_num=15,
        kind="reaction",
        bot_mood="вера",
        assistant_text="Я здесь.",
        user_text="Мне плохо.",
        question="Что случилось?",
        session_context="",
        reply_to_user_message_id=None,
    )
    regen_1 = face_actions.create_action(
        session_id="s3",
        q_num=16,
        answered_q_num=15,
        kind="regen",
        bot_mood="насмешка",
        assistant_text="Не слишком ли красиво страдаешь?",
        user_text="Мне плохо.",
        question="Что случилось?",
        session_context="",
        parent_token=token,
    )
    regen_2 = face_actions.create_action(
        session_id="s3",
        q_num=16,
        answered_q_num=15,
        kind="regen",
        bot_mood="ласка",
        assistant_text="Ну иди сюда, только не лги.",
        user_text="Мне плохо.",
        question="Что случилось?",
        session_context="",
        parent_token=regen_1,
    )

    used = face_actions.used_bot_moods(token)
    assert used == {"вера", "насмешка", "ласка"}
    assert face_actions.used_bot_moods(regen_2) == used

    next_face = moods.opposite_bot_mood("насмешка", exclude=used)
    assert next_face not in used
    assert moods.opposite_bot_mood("насмешка", exclude=set(moods.BOT_MOODS)) is None


def test_question_tokens_are_not_rateable(as_user):
    event = session_log.append(
        session_id="s4",
        role="assistant",
        kind="question",
        text="Что ты прячешь?",
        at="2026-05-25T12:00:00",
        message_id=444,
        q_num=17,
        domain="identity",
    )
    token = face_actions.create_remask_action(event, at="2026-05-25T12:01:00")
    rec = face_actions.get_action(token)

    assert face_actions.is_rateable(rec) is False
    assert face_actions.set_liked(token, liked=True) is None
    assert face_actions.record_user_score(token, 1.0, "favorite") is False
