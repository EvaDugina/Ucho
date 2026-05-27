from __future__ import annotations

import json
from datetime import datetime
from types import SimpleNamespace

import pytest

from bot import face_actions, handlers, moods, qmap, questions, session, session_log, sessions, userctx
from bot.errors import LLMError, VaultError
from bot.services import conversation_service, note_service


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


class _FakeBot:
    def __init__(self):
        self.sent: list[dict] = []

    async def send_message(self, chat_id, text, **kwargs):
        msg = SimpleNamespace(
            message_id=900 + len(self.sent),
            date=datetime(2026, 5, 26, 12, len(self.sent)),
        )
        self.sent.append({"chat_id": chat_id, "text": text, "kwargs": kwargs, "message": msg})
        return msg


class _FakeMessage:
    def __init__(self, text: str = "/ucho важное", *, bot: _FakeBot | None = None):
        self.text = text
        self.caption = None
        self.content_type = "text"
        self.message_id = 77
        self.date = datetime(2026, 5, 26, 12, 0)
        self.reply_to_message = None
        self.bot = bot or _FakeBot()
        self.chat = SimpleNamespace(id=123)
        self.answers: list[dict] = []

    async def answer(self, text, **kwargs):
        msg = SimpleNamespace(message_id=800 + len(self.answers), date=self.date)
        self.answers.append({"text": text, "kwargs": kwargs, "message": msg})
        return msg


@pytest.mark.asyncio
async def test_probe_does_not_call_llm_when_session_log_required_fails(as_user, monkeypatch):
    s = session.start(mode="probe", domain="everyday")
    session.set_question("Что важно?", "everyday", q_num=1)
    called = False

    async def fake_process_answer(**kwargs):
        nonlocal called
        called = True
        return {"observations": [], "reaction": "вижу"}

    def fail_append_required(**kwargs):
        raise VaultError("session log is unavailable")

    monkeypatch.setattr(conversation_service, "process_answer", fake_process_answer)
    monkeypatch.setattr(conversation_service.session_log, "append_required", fail_append_required)

    with pytest.raises(VaultError):
        await handlers._handle_probe_locked(_FakeMessage("ответ"), "ответ")

    assert called is False
    assert s.pending_answer_event_id is None


@pytest.mark.asyncio
async def test_probe_llm_error_answers_generated_fallback(as_user, monkeypatch):
    session.start(mode="probe", domain="everyday")
    session.set_question("Что важно?", "everyday", q_num=1)
    message = _FakeMessage("ответ")

    async def fail_process_probe_answer(*args, **kwargs):
        raise LLMError("down")

    monkeypatch.setattr(conversation_service, "process_probe_answer", fail_process_probe_answer)
    monkeypatch.setattr(handlers.moods.random, "choice", lambda seq: seq[0])

    await handlers._handle_probe_locked(message, "ответ")

    assert [a["text"] for a in message.answers] == [
        moods.LLM_ERROR_FALLBACK_REPLIES["раскачивание"]
    ]


@pytest.mark.asyncio
async def test_pebble_always_answers_static_bolno(as_user, monkeypatch):
    message = _FakeMessage("/pebble")
    message.from_user = SimpleNamespace(id=as_user)

    monkeypatch.setattr(handlers.users, "is_allowed", lambda uid: True)

    await handlers.cmd_pebble(message)

    assert [a["text"] for a in message.answers] == ["Больно."]


@pytest.mark.asyncio
async def test_regen_llm_error_answers_generated_fallback(as_user, monkeypatch):
    session_id = "s-regen-fallback"
    session_log.append(
        session_id=session_id,
        role="assistant",
        kind="question",
        text="Что болит?",
        at="2026-05-26T12:00:00",
        message_id=10,
        q_num=1,
        domain="everyday",
    )
    session_log.append(
        session_id=session_id,
        role="user",
        kind="answer",
        text="Болит язык.",
        at="2026-05-26T12:01:00",
        message_id=11,
        reply_to_message_id=10,
        q_num=1,
        domain="everyday",
    )
    session_log.append(
        session_id=session_id,
        role="assistant",
        kind="reaction",
        text="Старый комментарий.",
        at="2026-05-26T12:02:00",
        message_id=12,
        reply_to_message_id=11,
        q_num=1,
        domain="everyday",
        bot_mood="сомнение",
    )
    token = face_actions.create_action(
        session_id=session_id,
        q_num=1,
        answered_q_num=1,
        kind="reaction",
        bot_mood="сомнение",
        assistant_text="Старый комментарий.",
        user_text="Болит язык.",
        question="Что болит?",
        session_context="",
        reply_to_user_message_id=11,
    )
    face_actions.set_message(token, 12)
    message = _FakeMessage("/regen")
    message.from_user = SimpleNamespace(id=as_user)
    message.reply_to_message = SimpleNamespace(message_id=12)

    async def fail_regenerate_reaction(*args, **kwargs):
        raise LLMError("down")

    monkeypatch.setattr(handlers.users, "is_allowed", lambda uid: True)
    monkeypatch.setattr(handlers, "regenerate_reaction", fail_regenerate_reaction)
    monkeypatch.setattr(handlers.moods.random, "choice", lambda seq: seq[0])

    await handlers.cmd_regen(message, SimpleNamespace(args=None))

    assert moods.LLM_ERROR_FALLBACK_REPLIES["раскачивание"] in message.answers[-1]["text"]
    rec = face_actions.find_by_message_id(message.answers[-1]["message"].message_id)
    assert rec is not None
    assert rec["kind"] == "regen"


@pytest.mark.asyncio
async def test_ask_next_llm_error_is_silent_to_user(as_user, monkeypatch):
    bot = _FakeBot()
    session.start(mode="probe", domain="everyday")

    async def fail_ask_next(**kwargs):
        raise LLMError("down")

    monkeypatch.setattr(handlers, "ask_next", fail_ask_next)

    await handlers._send_next_question(bot, 123, domain="everyday")

    assert bot.sent == []


@pytest.mark.asyncio
async def test_ingest_note_reacts_without_saved_status(as_user, monkeypatch):
    bot = _FakeBot()
    message = _FakeMessage("/ucho держи мысль", bot=bot)
    captured = {}

    async def fake_process_answer(**kwargs):
        captured.update(kwargs)
        return {"observations": [], "reaction": "Вот теперь слышу трещину.", "user_delta": {}}

    monkeypatch.setattr(note_service, "process_answer", fake_process_answer)

    await handlers._ingest_note(message, "держи мысль")

    note_files = list((userctx.user_root() / "00_raw" / "notes").glob("*.md"))
    note_text = "\n".join(p.read_text(encoding="utf-8") for p in note_files)
    assert "держи мысль" in note_text
    assert bot.sent
    assert "Вот теперь слышу трещину." in bot.sent[-1]["text"]
    assert "<i>" in bot.sent[-1]["text"]
    assert "лицо Иуды" not in bot.sent[-1]["text"]
    assert "reply_markup" not in bot.sent[-1]["kwargs"]
    rec = face_actions.find_by_message_id(bot.sent[-1]["message"].message_id)
    assert rec is not None
    assert rec["kind"] == "reaction"
    assert captured["bot_mood"] in moods.BOT_MOODS
    assert "Заметка сохранена" not in bot.sent[-1]["text"]
    assert "+0" not in bot.sent[-1]["text"]
    assert message.answers == []


@pytest.mark.asyncio
async def test_ingest_note_silent_when_llm_fails_after_note_saved(as_user, monkeypatch):
    bot = _FakeBot()
    message = _FakeMessage("/ucho держи мысль", bot=bot)

    async def fail_process_answer(**kwargs):
        raise LLMError("down")

    monkeypatch.setattr(note_service, "process_answer", fail_process_answer)

    await handlers._ingest_note(message, "держи мысль")

    note_files = list((userctx.user_root() / "00_raw" / "notes").glob("*.md"))
    note_text = "\n".join(p.read_text(encoding="utf-8") for p in note_files)
    assert "держи мысль" in note_text
    assert bot.sent == []
    assert message.answers == []


@pytest.mark.asyncio
async def test_ingest_note_does_not_call_llm_when_note_write_fails(as_user, monkeypatch):
    message = _FakeMessage("/ucho держи мысль")
    called = False

    def fail_append_note(*args, **kwargs):
        raise VaultError("cannot write note")

    async def fake_process_answer(**kwargs):
        nonlocal called
        called = True
        return {"observations": [], "reaction": "не должен"}

    monkeypatch.setattr(note_service.vault, "append_note", fail_append_note)
    monkeypatch.setattr(note_service, "process_answer", fake_process_answer)

    await handlers._ingest_note(message, "держи мысль")

    assert called is False
    assert message.answers
    assert "записать заметку" in message.answers[-1]["text"]
