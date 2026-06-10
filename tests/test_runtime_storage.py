from __future__ import annotations

import json
from datetime import datetime
from types import SimpleNamespace

import pytest

from bot import face_actions, handlers, middleware, moods, qmap, questions, session, session_log, sessions, userctx, vault
from bot.config import DOMAINS
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


def test_session_json_keeps_queued_answer_and_restore(as_user):
    session.start(mode="probe", domain="everyday")
    q_num = vault.next_q_num()
    session.set_question("Что держит?", "everyday", q_num=q_num)

    session.enqueue_answer(
        "первый кусок",
        message_id=31,
        at="2026-05-25T11:01:00",
        source="text",
    )
    session.enqueue_answer(
        "второй кусок",
        message_id=32,
        at="2026-05-25T11:02:00",
        source="echo",
    )

    data = json.loads((userctx.user_root() / "_session.json").read_text(encoding="utf-8"))
    assert data["queued_answer"]["text"] == "первый кусок\n\nвторой кусок"
    assert data["queued_answer"]["question"] == "Что держит?"
    assert [f["source"] for f in data["queued_answer"]["fragments"]] == ["text", "echo"]

    session._active.pop(as_user, None)
    restored = dict(session.restore_all())
    assert session.has_queued(restored[as_user])
    assert restored[as_user].queued_answer["origin_q_num"] == q_num


class _FakeBot:
    def __init__(self):
        self.sent: list[dict] = []
        self.deleted: list[dict] = []
        self.fail_delete_ids: set[int] = set()

    async def send_message(self, chat_id, text, **kwargs):
        msg = SimpleNamespace(
            message_id=900 + len(self.sent),
            date=datetime(2026, 5, 26, 12, len(self.sent)),
        )
        self.sent.append({"chat_id": chat_id, "text": text, "kwargs": kwargs, "message": msg})
        return msg

    async def delete_message(self, chat_id, message_id):
        if int(message_id) in self.fail_delete_ids:
            raise RuntimeError("telegram refused")
        self.deleted.append({"chat_id": chat_id, "message_id": int(message_id)})
        return True


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
async def test_leta_without_args_warns_and_keeps_data(as_user, monkeypatch):
    marker = userctx.user_root() / "00_raw" / "notes" / "keep.md"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("не трогать\n", encoding="utf-8")
    monkeypatch.setattr(handlers.users, "is_allowed", lambda uid: True)
    message = _FakeMessage("/leta")
    message.from_user = SimpleNamespace(id=as_user)

    await handlers.cmd_leta(message, SimpleNamespace(args=None))

    assert marker.exists()
    answer = message.answers[-1]["text"]
    assert f"/leta УДАЛИТЬ {as_user}" in answer
    assert "git history" in answer


@pytest.mark.asyncio
async def test_leta_invalid_confirmation_keeps_data(as_user, monkeypatch):
    marker = userctx.user_root() / "00_raw" / "notes" / "keep.md"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("не трогать\n", encoding="utf-8")
    monkeypatch.setattr(handlers.users, "is_allowed", lambda uid: True)
    message = _FakeMessage("/leta УДАЛИТЬ не-то")
    message.from_user = SimpleNamespace(id=as_user)

    await handlers.cmd_leta(message, SimpleNamespace(args=f"УДАЛИТЬ {as_user + 1}"))

    assert marker.exists()
    assert "Не удалил" in message.answers[-1]["text"]
    assert f"/leta УДАЛИТЬ {as_user}" in message.answers[-1]["text"]


@pytest.mark.asyncio
async def test_leta_confirmation_clears_current_user_data(as_user, monkeypatch):
    root = userctx.user_root()
    marker = root / "00_raw" / "notes" / "delete.md"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("удалить\n", encoding="utf-8")
    stale_files = [
        root / "00_raw" / "sessions" / "old.jsonl",
        root / "00_raw" / "qna" / "old.md",
        root / "01_mood" / "events" / "old.jsonl",
        root / "01_mood" / "timeseries" / "old.jsonl",
        root / "02_concepts" / "everyday" / "old.md",
        root / "02_digest" / "old.md",
        root / "03_personality" / "liked_replies.json",
        root / "_state.json",
    ]
    for stale in stale_files:
        stale.parent.mkdir(parents=True, exist_ok=True)
        if stale.name == "_state.json":
            stale.write_text('{"last_q_num": 99}\n', encoding="utf-8")
        else:
            stale.write_text("stale\n", encoding="utf-8")
    session.start(mode="probe", domain="everyday")
    session.set_question("Что забыть?", "everyday", q_num=vault.next_q_num())
    monkeypatch.setattr(handlers.users, "is_allowed", lambda uid: True)
    monkeypatch.setattr(handlers, "LETA_CHAT_PURGE_DELAY_SECONDS", 0)
    message = _FakeMessage(f"/leta УДАЛИТЬ {as_user}")
    message.from_user = SimpleNamespace(id=as_user)

    await handlers.cmd_leta(message, SimpleNamespace(args=f"УДАЛИТЬ {as_user}"))

    assert root.exists()
    assert not marker.exists()
    assert (root / "00_raw" / "sessions").is_dir()
    assert not any((root / "00_raw" / "sessions").glob("*.jsonl"))
    assert not any((root / "00_raw" / "qna").glob("*.md"))
    assert not any((root / "00_raw" / "notes").glob("*.md"))
    assert not any((root / "01_mood" / "events").glob("*.jsonl"))
    assert not any((root / "01_mood" / "timeseries").glob("*.jsonl"))
    assert not any((root / "02_concepts").rglob("*.md"))
    assert not any((root / "02_digest").glob("*.md"))
    assert not (root / "03_personality" / "liked_replies.json").exists()
    assert not (root / "_state.json").exists()
    assert not (root / "_session.json").exists()
    assert (root / "_index.md").exists()
    assert (root / ".obsidian" / "graph.json").exists()
    assert (root / "03_personality" / "about.md").exists()
    assert (root / "03_personality" / "mood.md").exists()
    for domain in DOMAINS:
        assert (root / "02_profile" / f"{domain}.md").exists()
    assert session.get() is None
    assert [a["text"] for a in message.answers] == [
        "Смываю твоё дерьмо в унитаз сраный подонок.",
        "Кончил.",
    ]
    deleted_ids = {item["message_id"] for item in message.bot.deleted}
    assert {77, 800}.issubset(deleted_ids)
    assert 801 not in deleted_ids


@pytest.mark.asyncio
async def test_leta_failure_keeps_error_message_and_does_not_purge_chat(as_user, monkeypatch):
    monkeypatch.setattr(handlers.users, "is_allowed", lambda uid: True)

    def fail_delete():
        raise VaultError("no")

    monkeypatch.setattr(handlers.deletion_service, "delete_current_user_data", fail_delete)
    message = _FakeMessage(f"/leta УДАЛИТЬ {as_user}")
    message.from_user = SimpleNamespace(id=as_user)

    await handlers.cmd_leta(message, SimpleNamespace(args=f"УДАЛИТЬ {as_user}"))

    assert [a["text"] for a in message.answers] == [
        "Смываю твоё дерьмо в унитаз сраный подонок.",
        "Не удалил: проверка безопасности не прошла.",
    ]
    assert message.bot.deleted == []


@pytest.mark.asyncio
async def test_leta_chat_purge_deletes_in_order_with_delay_and_ignores_failures(monkeypatch):
    delays: list[float] = []

    async def fake_sleep(delay):
        delays.append(delay)

    monkeypatch.setattr(handlers.asyncio, "sleep", fake_sleep)
    bot = _FakeBot()
    bot.fail_delete_ids = {2}
    message = _FakeMessage(bot=bot)
    message.from_user = SimpleNamespace(id=123)

    await handlers._delete_chat_messages_after_leta(
        message,
        [3, None, 1, 2, 2],
        delete_delay=0.05,
    )

    assert [item["message_id"] for item in bot.deleted] == [1, 3]
    assert delays == [0.05, 0.05, 0.05]


@pytest.mark.asyncio
async def test_start_only_clears_session_and_keeps_data(as_user, monkeypatch):
    marker = userctx.user_root() / "00_raw" / "notes" / "keep.md"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("не трогать\n", encoding="utf-8")
    session.start(mode="probe", domain="everyday")
    monkeypatch.setattr(handlers.users, "is_allowed", lambda uid: True)
    message = _FakeMessage("/start")
    message.from_user = SimpleNamespace(id=as_user)

    await handlers.cmd_start(message)

    assert marker.exists()
    assert session.get() is None
    assert "Данные целы" in message.answers[-1]["text"]


@pytest.mark.asyncio
async def test_busy_text_and_echo_merge_into_cancelable_queue(as_user, monkeypatch):
    session.start(mode="probe", domain="everyday")
    session.set_question("Что держит?", "everyday", q_num=vault.next_q_num())
    session.get().pending_answer_event_id = "already-in-llm"
    session.persist()
    monkeypatch.setattr(handlers.users, "is_allowed", lambda uid: True)

    text_msg = _FakeMessage("первый кусок")
    text_msg.from_user = SimpleNamespace(id=as_user)
    await handlers.on_text(text_msg)

    echo_msg = _FakeMessage("/echo второй кусок")
    echo_msg.from_user = SimpleNamespace(id=as_user)
    await handlers.cmd_echo(echo_msg, SimpleNamespace(args="второй кусок"))

    queued = session.get().queued_answer
    assert queued["text"] == "первый кусок\n\nвторой кусок"
    assert [f["source"] for f in queued["fragments"]] == ["text", "echo"]
    assert text_msg.answers[-1]["text"] == "Ещё думаю."
    assert echo_msg.answers[-1]["text"] == "Ещё думаю."

    cancel_msg = _FakeMessage("/cancel")
    cancel_msg.from_user = SimpleNamespace(id=as_user)
    await handlers.cmd_cancel(cancel_msg)

    assert session.get().queued_answer is None
    assert session.get().pending_answer_event_id == "already-in-llm"
    assert cancel_msg.answers[-1]["text"] == "Я удалил тебя из памяти"


@pytest.mark.asyncio
async def test_busy_command_middleware_replies_and_keeps_session(as_user, monkeypatch):
    session.start(mode="probe", domain="everyday")
    session.set_question("Что держит?", "everyday", q_num=vault.next_q_num())
    session.get().pending_answer_event_id = "already-in-llm"
    session.persist()
    message = _FakeMessage("/ask")
    message.from_user = SimpleNamespace(id=as_user)
    called = False

    async def handler(event, data):
        nonlocal called
        called = True

    monkeypatch.setattr(middleware.users, "is_allowed", lambda uid: True)
    monkeypatch.setattr(middleware.users, "is_owner", lambda uid: True)
    monkeypatch.setattr(middleware, "Message", _FakeMessage)

    await middleware.AccessMiddleware()(handler, message, {"event_from_user": message.from_user})

    assert called is False
    assert message.answers[-1]["text"] == "Ещё думаю."
    assert session.get() is not None
    assert session.get().last_question == "Что держит?"


@pytest.mark.asyncio
async def test_busy_pebble_middleware_reaches_handler_and_keeps_session(as_user, monkeypatch):
    session.start(mode="probe", domain="everyday")
    session.set_question("Что держит?", "everyday", q_num=vault.next_q_num())
    session.get().pending_answer_event_id = "already-in-llm"
    session.persist()
    message = _FakeMessage("/pebble")
    message.from_user = SimpleNamespace(id=as_user)
    called = False

    async def handler(event, data):
        nonlocal called
        called = True
        await handlers.cmd_pebble(event)

    monkeypatch.setattr(middleware.users, "is_allowed", lambda uid: True)
    monkeypatch.setattr(middleware.users, "is_owner", lambda uid: True)
    monkeypatch.setattr(middleware, "Message", _FakeMessage)

    await middleware.AccessMiddleware()(handler, message, {"event_from_user": message.from_user})

    assert called is True
    assert message.answers[-1]["text"] == "Больно."
    assert session.get() is not None
    assert session.get().last_question == "Что держит?"


@pytest.mark.asyncio
async def test_busy_leta_middleware_replies_and_keeps_session(as_user, monkeypatch):
    session.start(mode="probe", domain="everyday")
    session.set_question("Что держит?", "everyday", q_num=vault.next_q_num())
    session.get().pending_answer_event_id = "already-in-llm"
    session.persist()
    message = _FakeMessage(f"/leta УДАЛИТЬ {as_user}")
    message.from_user = SimpleNamespace(id=as_user)
    called = False

    async def handler(event, data):
        nonlocal called
        called = True

    monkeypatch.setattr(middleware.users, "is_allowed", lambda uid: True)
    monkeypatch.setattr(middleware.users, "is_owner", lambda uid: True)
    monkeypatch.setattr(middleware, "Message", _FakeMessage)

    await middleware.AccessMiddleware()(handler, message, {"event_from_user": message.from_user})

    assert called is False
    assert message.answers[-1]["text"] == "Ещё думаю."
    assert session.get() is not None
    assert session.get().last_question == "Что держит?"


@pytest.mark.asyncio
async def test_leta_warning_middleware_does_not_close_session(as_user, monkeypatch):
    session.start(mode="probe", domain="everyday")
    session.set_question("Что держит?", "everyday", q_num=vault.next_q_num())
    message = _FakeMessage("/leta")
    message.from_user = SimpleNamespace(id=as_user)
    called = False

    async def handler(event, data):
        nonlocal called
        called = True

    monkeypatch.setattr(middleware.users, "is_allowed", lambda uid: True)
    monkeypatch.setattr(middleware.users, "is_owner", lambda uid: True)
    monkeypatch.setattr(middleware, "Message", _FakeMessage)

    await middleware.AccessMiddleware()(handler, message, {"event_from_user": message.from_user})

    assert called is True
    assert session.get() is not None
    assert session.get().last_question == "Что держит?"


@pytest.mark.asyncio
async def test_drain_queued_answer_uses_old_question_snapshot(as_user, monkeypatch):
    session.start(mode="probe", domain="everyday")
    origin_q = vault.next_q_num()
    session.set_question("Старый вопрос?", "everyday", q_num=origin_q)
    session.enqueue_answer("досланный текст", message_id=41, at="2026-05-25T11:05:00")
    reaction_q = vault.next_q_num()
    session.set_question("Текущий комментарий.", "everyday", q_num=reaction_q)
    message = _FakeMessage("carrier")
    message.from_user = SimpleNamespace(id=as_user)
    captured = {}

    async def fake_process_probe_answer(text, **kwargs):
        captured["text"] = text
        captured.update(kwargs)
        return conversation_service.ReactionPayload(
            q_num=999,
            mode="probe",
            domain="everyday",
            text="ответ",
            bot_mood=None,
            answered_q_num=kwargs["q_num"],
            answered_question=kwargs["question"],
            session_id=session.get().id,
            user_text=text,
            session_context=kwargs["session_context_snapshot"],
            reply_to_user_message_id=kwargs["message_id"],
        )

    async def fake_send_payload(message, payload):
        captured["payload"] = payload

    monkeypatch.setattr(handlers.conversation_service, "process_probe_answer", fake_process_probe_answer)
    monkeypatch.setattr(handlers, "_send_reaction_payload", fake_send_payload)
    monkeypatch.setattr(handlers.vault, "commit_all", lambda *args, **kwargs: "sha")

    await handlers._drain_queued_answers(message, is_owner=False)

    assert captured["text"] == "досланный текст"
    assert captured["question"] == "Старый вопрос?"
    assert captured["q_num"] > reaction_q
    assert session.get().queued_answer is None


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
async def test_ask_next_commits_after_successful_question(as_user, monkeypatch):
    bot = _FakeBot()
    session.start(mode="probe", domain="everyday")
    commits: list[str] = []

    async def fake_ask_next(**kwargs):
        _ = kwargs
        return {"question": "Что держит форму?", "domain": "everyday"}

    monkeypatch.setattr(handlers, "ask_next", fake_ask_next)
    monkeypatch.setattr(
        handlers.vault,
        "commit_all",
        lambda message, allow_empty=False: commits.append(message) or "sha",
    )

    await handlers._send_next_question(bot, 123, domain="everyday")

    assert bot.sent
    assert commits == ["ask question"]


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
