from __future__ import annotations

import json
from datetime import datetime

from bot import inbox, recovery, session, userctx, vault


def _open_probe(q_num: int = 7):
    s = session.start(mode="probe", domain="everyday")
    session.set_question("Что ты видишь?", "everyday", q_num=q_num)
    s = session.get()
    s.add_message_id(10)
    s.record_assistant("Что ты видишь?", at="2026-05-25T10:00:00")
    return s


def test_inbox_writes_full_user_text_with_session_snapshot(as_user):
    s = _open_probe()

    inbox.append(
        kind="text",
        text="Полный ответ пользователя.",
        at="2026-05-25T10:01:00",
        message_id=11,
        chat_id=as_user,
        session_id=s.id,
        session_mode=s.mode,
        q_num=s.current_q_num,
        domain=s.last_domain,
    )

    path = userctx.user_root() / "raw" / "inbox" / "2026-05-25.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

    assert rows[0]["text"] == "Полный ответ пользователя."
    assert rows[0]["session_id"] == s.id
    assert rows[0]["q_num"] == 7
    assert inbox.latest_text_for_session(s.id)["message_id"] == 11


def test_unanswered_inbox_text_becomes_pending_answer(as_user):
    s = _open_probe()
    inbox.append(
        kind="text",
        text="Ответ, который дошёл только до inbox.",
        at="2026-05-25T10:01:00",
        message_id=11,
        session_id=s.id,
        session_mode=s.mode,
        q_num=s.current_q_num,
        domain=s.last_domain,
    )

    recoveries = recovery.mark_unanswered_inbox_as_pending([(as_user, s)])

    assert recoveries[0].action == "pending"
    assert session.get().pending_answer == "Ответ, который дошёл только до inbox."


def test_inbox_text_is_not_recovered_when_bot_already_answered(as_user):
    s = _open_probe()
    inbox.append(
        kind="text",
        text="Уже отвеченный текст.",
        at="2026-05-25T10:01:00",
        message_id=11,
        session_id=s.id,
        q_num=s.current_q_num,
        domain=s.last_domain,
    )
    s.add_message_id(12)
    s.record_assistant("Реакция уже была.", at="2026-05-25T10:02:00")

    assert recovery.mark_unanswered_inbox_as_pending([(as_user, s)]) == []
    assert session.get().pending_answer is None


def test_inbox_text_with_existing_raw_gets_notify_saved_recovery(as_user):
    s = _open_probe()
    inbox.append(
        kind="text",
        text="Сохранённый, но без реплики.",
        at="2026-05-25T10:01:00",
        message_id=11,
        session_id=s.id,
        q_num=s.current_q_num,
        domain=s.last_domain,
    )
    vault.append_raw(
        7,
        datetime(2026, 5, 25, 10, 1),
        "everyday",
        "Что ты видишь?",
        "Сохранённый, но без реплики.",
    )

    recoveries = recovery.mark_unanswered_inbox_as_pending([(as_user, s)])

    assert recoveries[0].action == "notify_saved"
    assert session.get().pending_answer is None
