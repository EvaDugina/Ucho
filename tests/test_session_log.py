from __future__ import annotations

import json
import shutil

import pytest

from bot.errors import VaultError
from bot import session_log, userctx


def test_session_log_writes_one_jsonl_file_per_session(as_user):
    session_log.append(
        session_id="abc123",
        role="user",
        kind="answer",
        text="Первое.",
        at="2026-05-25T10:00:00",
        message_id=1,
        q_num=7,
        domain="everyday",
    )
    session_log.append(
        session_id="abc123",
        role="assistant",
        kind="reaction",
        text="Второе.",
        at="2026-05-25T10:01:00",
        message_id=2,
        reply_to_message_id=1,
        q_num=8,
        domain="everyday",
        bot_mood="вера",
    )

    path = userctx.user_root() / "00_raw" / "sessions" / "abc123.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert [r["text"] for r in rows] == ["Первое.", "Второе."]
    assert rows[0]["event_id"] == "abc123:000001"
    assert rows[0]["telegram_message_id"] == 1
    assert rows[0]["ts"] == "2026-05-25T10:00:00"
    assert rows[1]["reply_to_message_id"] == 1
    assert rows[1]["bot_mood"] == "вера"


def test_session_log_required_raises_on_write_error(as_user):
    sessions = userctx.user_root() / "00_raw" / "sessions"
    shutil.rmtree(sessions)
    sessions.write_text("not a directory", encoding="utf-8")

    with pytest.raises(VaultError):
        session_log.append_required(
            session_id="required",
            role="user",
            kind="answer",
            text="Не терять.",
        )
