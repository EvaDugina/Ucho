from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from bot import about, moods, recovery, session, session_log, userctx, vault
from bot.errors import LLMError, VaultError
from bot.services import conversation_service


def _mood():
    return {
        "sign": "-",
        "energy": "high",
        "direction": "auto",
        "quality": "тревога",
        "dominance": "low",
    }


def test_session_log_rejects_unsafe_session_id(as_user):
    with pytest.raises(VaultError):
        session_log.append_required(
            session_id="../../escape",
            role="user",
            kind="answer",
            text="Не должно записаться",
        )
    assert not (userctx.user_root() / "00_raw" / "escape.jsonl").exists()


def test_user_write_requires_current_uid(as_user):
    token = userctx._current_uid.set(None)
    entered = False
    try:
        with pytest.raises(VaultError), vault.user_write("unsafe unscoped write"):
            entered = True
    finally:
        userctx._current_uid.reset(token)
    assert entered is False


@pytest.mark.asyncio
async def test_raw_before_llm_and_mood_personality_outputs(as_user, monkeypatch):
    current = session.start(domain="ethics")
    session.set_question("Что для тебя честность?", "ethics", q_num=1)
    observed = {}

    async def classify(*args, **kwargs):
        return _mood()

    async def process(*args, **kwargs):
        events = session_log.session_events(current.id)
        observed["raw_seen"] = events[-1]["kind"] == "answer"
        observed["event_id"] = events[-1]["event_id"]
        observed["mood"] = kwargs.get("mood")
        return {
            "reaction": "Не прячься за словом.",
            "personality_delta": [
                {
                    "aspect": "values",
                    "summary": "Ставит честность выше удобства.",
                    "quote": "честность выше удобства",
                    "confidence": 0.9,
                }
            ],
        }

    monkeypatch.setattr(conversation_service, "classify_mood", classify)
    monkeypatch.setattr(conversation_service, "process_answer", process)
    payload = await conversation_service.process_probe_answer(
        "Для меня честность выше удобства.",
        message_id=101,
    )
    assert payload is not None
    assert observed["raw_seen"] is True
    assert observed["mood"] == _mood()
    assert session.get().pending_answer_event_id is None
    delta = about.pending_deltas()[0]
    assert delta["raw_event_id"] == observed["event_id"]
    assert delta["status"] == "pending"
    root = userctx.user_root()
    assert (root / "01_mood" / "current.md").exists()
    mood_events = list((root / "01_mood" / "events").glob("*.jsonl"))
    assert mood_events
    row = json.loads(mood_events[0].read_text(encoding="utf-8").splitlines()[0])
    assert row["raw_event_id"] == observed["event_id"]
    for legacy in ("qna", "notes", "02_concepts", "03_personality"):
        assert not (root / legacy).exists()


@pytest.mark.asyncio
async def test_failed_llm_keeps_raw_pending_for_recovery(as_user, monkeypatch):
    current = session.start(domain="everyday")
    session.set_question("Что случилось?", "everyday", q_num=1)

    async def classify(*args, **kwargs):
        return _mood()

    async def fail(*args, **kwargs):
        raise LLMError("offline")

    monkeypatch.setattr(conversation_service, "classify_mood", classify)
    monkeypatch.setattr(conversation_service, "process_answer", fail)
    with pytest.raises(LLMError):
        await conversation_service.process_probe_answer("Я устал.", message_id=55)
    assert session.get().pending_answer == "Я устал."
    event_id = session.get().pending_answer_event_id
    assert session_log.find_event(event_id)["text"] == "Я устал."
    assert len(session_log.session_events(current.id)) == 1
    mood_events = list((userctx.user_root() / "01_mood" / "events").glob("*.jsonl"))
    assert len(mood_events) == 1
    assert len(mood_events[0].read_text(encoding="utf-8").splitlines()) == 1


@pytest.mark.asyncio
async def test_billing_failure_replies_to_requester_and_keeps_raw(as_user, monkeypatch):
    from bot import handlers

    current = session.start(domain="everyday")
    session.set_question("Что случилось?", "everyday", q_num=1)

    async def classify(*args, **kwargs):
        return _mood()

    async def fail(*args, **kwargs):
        raise LLMError("payment required", billing=True)

    monkeypatch.setattr(conversation_service, "classify_mood", classify)
    monkeypatch.setattr(conversation_service, "process_answer", fail)
    replies = []

    async def answer(text):
        replies.append(text)

    message = SimpleNamespace(
        answer=answer, bot=SimpleNamespace(), chat=SimpleNamespace(id=as_user),
        message_id=56, date=None, reply_to_message=None,
    )
    await handlers._process_current_text(message, "Я устал.")

    assert replies == ["Я без денег."]
    assert session.get().pending_answer == "Я устал."
    assert session_log.find_event(session.get().pending_answer_event_id)["text"] == "Я устал."
    assert session.get().id == current.id


@pytest.mark.asyncio
async def test_failed_manual_question_preserves_current_session(as_user, monkeypatch):
    from bot import handlers

    current = session.start(domain="ethics")
    session.set_question("Текущий вопрос", "ethics", q_num=1)

    async def fail(*args, **kwargs):
        raise LLMError("offline")

    monkeypatch.setattr(handlers, "ask_next", fail)

    class Bot:
        async def send_chat_action(self, *args, **kwargs):
            return None

    with pytest.raises(LLMError):
        await handlers._generate_question(Bot(), as_user, domain="work")
    assert session.get().id == current.id
    assert session.get().last_question == "Текущий вопрос"


@pytest.mark.asyncio
async def test_failed_daily_question_preserves_current_session(as_user, monkeypatch):
    from bot.services import daily_service

    current = session.start(domain="ethics")
    session.set_question("Текущий вопрос", "ethics", q_num=1)

    async def fail(*args, **kwargs):
        raise LLMError("offline")

    monkeypatch.setattr(daily_service, "ask_next", fail)
    monkeypatch.setattr(daily_service.users, "is_allowed", lambda _: True)
    monkeypatch.setattr(daily_service.vault, "daily_already_sent", lambda _: False)

    class Bot:
        async def send_chat_action(self, *args, **kwargs):
            return None

    assert await daily_service.send_daily_question(Bot(), as_user) is False
    assert session.get().id == current.id
    assert session.get().last_question == "Текущий вопрос"


@pytest.mark.asyncio
async def test_note_service_uses_current_process_contract(as_user, monkeypatch):
    from bot.services import note_service

    captured = {}

    async def process(text, **kwargs):
        captured.update({"text": text, **kwargs})
        return None

    monkeypatch.setattr(note_service, "process_probe_answer", process)
    await note_service.ingest_note("Свободная заметка", message_id=42)
    assert captured["text"] == "Свободная заметка"
    assert captured["event_kind"] == "note"
    assert "asked_at" not in captured


@pytest.mark.asyncio
async def test_ucho_starts_new_context_and_followup_stays_there(as_user, monkeypatch):
    from bot.services import note_service

    old = session.start(domain="ethics")
    session.set_question("Старый вопрос", "ethics", q_num=7)
    session_log.append_required(
        session_id=old.id,
        role="assistant",
        kind="question",
        text="Старый вопрос",
        q_num=7,
        domain="ethics",
    )
    prompts = []

    async def classify(*args, **kwargs):
        return _mood()

    async def process(*, question, answer, session_context, **kwargs):
        prompts.append((question, answer, session_context))
        return {"reaction": "Расскажи подробнее.", "personality_delta": []}

    monkeypatch.setattr(conversation_service, "classify_mood", classify)
    monkeypatch.setattr(conversation_service, "process_answer", process)

    await note_service.ingest_note("Новая тема", message_id=42)
    new = session.get()
    assert new is not None and new.id != old.id
    old_events = session_log.session_events(old.id)
    new_events = session_log.session_events(new.id)
    assert len(old_events) == 1
    assert len(new_events) == 1
    assert new_events[0]["kind"] == "note"
    assert new_events[0]["q_num"] is None
    assert new_events[0]["metadata"]["source"] == "ucho"
    assert prompts[0][0] == "(свободная заметка)"
    assert "Новая тема" in prompts[0][2]
    assert "Старый вопрос" not in prompts[0][2]

    session_log.append_required(
        session_id=new.id,
        role="assistant",
        kind="reaction",
        text="Расскажи подробнее.",
        q_num=new.current_q_num,
        domain="everyday",
    )
    await conversation_service.process_probe_answer("Продолжение", message_id=43)
    followup = session_log.session_events(new.id)[2]
    assert followup["kind"] == "answer"
    assert followup["q_num"] == new.current_q_num - 1
    assert len(session_log.session_events(old.id)) == 1
    assert prompts[1][0] == "Расскажи подробнее."
    assert "Новая тема" in prompts[1][2]
    assert "Продолжение" in prompts[1][2]
    assert "Старый вопрос" not in prompts[1][2]


@pytest.mark.asyncio
async def test_pending_ucho_recovers_as_note_in_new_session(as_user, monkeypatch):
    from bot.services import note_service

    old = session.start(domain="ethics")
    session.set_question("Старый вопрос", "ethics", q_num=7)

    async def classify(*args, **kwargs):
        return _mood()

    async def fail(*args, **kwargs):
        raise LLMError("offline")

    monkeypatch.setattr(conversation_service, "classify_mood", classify)
    monkeypatch.setattr(conversation_service, "process_answer", fail)
    with pytest.raises(LLMError):
        await note_service.ingest_note("Новая тема", message_id=42)
    new = session.get()
    assert new is not None and new.id != old.id
    assert session.has_pending(new)
    assert session_log.session_events(new.id)[0]["q_num"] is None

    observed = {}

    async def process(*, question, session_context, **kwargs):
        observed.update(question=question, session_context=session_context)
        return {"reaction": "Расскажи подробнее.", "personality_delta": []}

    class BotStub:
        async def send_message(self, *args, **kwargs):
            return SimpleNamespace(message_id=77, date=None)

    monkeypatch.setattr(recovery, "classify_mood", classify)
    monkeypatch.setattr(recovery, "process_answer", process)
    monkeypatch.setattr(recovery.users, "is_allowed", lambda _: True)
    await recovery.process_pending_on_startup(BotStub(), as_user)
    assert observed["question"] == "(свободная заметка)"
    assert "Новая тема" in observed["session_context"]
    assert "Старый вопрос" not in observed["session_context"]
    assert session.has_pending(new) is False
    assert [e["kind"] for e in session_log.session_events(new.id)] == ["note", "reaction"]


@pytest.mark.asyncio
async def test_busy_non_answer_generation_does_not_queue_old_session(
    as_user,
    monkeypatch,
):
    from bot import handlers

    session.start(domain="ethics")
    session.set_question("Старый вопрос", "ethics", q_num=1)
    monkeypatch.setattr(handlers.ratelimit, "is_inflight", lambda _: True)
    replies = []

    async def answer(text):
        replies.append(text)

    message = SimpleNamespace(answer=answer)
    await handlers._process_current_text(message, "Ответ не на тот вопрос")
    assert session.has_queued() is False
    assert replies == [handlers.ratelimit.BUSY_MESSAGE]


@pytest.mark.asyncio
async def test_recovery_processes_existing_event_without_duplicate(as_user, monkeypatch):
    current = session.start(domain="everyday")
    session.set_question("Что случилось?", "everyday", q_num=1)
    event = session_log.append_required(
        session_id=current.id,
        role="user",
        kind="answer",
        text="Я устал.",
        q_num=1,
        domain="everyday",
        message_id=55,
    )
    current.pending_answer = "Я устал."
    current.pending_answer_event_id = event["event_id"]
    session.persist()
    moods.log_turn(
        {
            **_mood(),
            "valence": -0.3,
            "arousal": 0.2,
            "dominance_label": "low",
            "stability": "adequate",
            "n": 1,
        },
        raw_event_id=str(event["event_id"]),
        session_id=current.id,
        q_num=1,
        at=event["ts"],
    )

    async def classify(*args, **kwargs):
        return _mood()

    async def process(*args, **kwargs):
        return {
            "reaction": "Отдохни.",
            "personality_delta": [
                {
                    "aspect": "emotional_regulation",
                    "summary": "Прямо сообщает об усталости.",
                    "quote": "Я устал",
                    "confidence": 0.8,
                }
            ],
        }

    class BotStub:
        async def send_message(self, *args, **kwargs):
            return SimpleNamespace(message_id=77, date=None)

    monkeypatch.setattr(recovery, "classify_mood", classify)
    monkeypatch.setattr(recovery, "process_answer", process)
    monkeypatch.setattr(recovery.users, "is_allowed", lambda _: True)
    await recovery.process_pending_on_startup(BotStub(), as_user)
    user_events = [
        item for item in session_log.session_events(current.id) if item["role"] == "user"
    ]
    assert len(user_events) == 1
    assert session.get().pending_answer_event_id is None
    assert about.pending_deltas()[0]["raw_event_id"] == event["event_id"]
    mood_events = list((userctx.user_root() / "01_mood" / "events").glob("*.jsonl"))
    assert sum(
        len(path.read_text(encoding="utf-8").splitlines()) for path in mood_events
    ) == 1


@pytest.mark.asyncio
async def test_durable_queue_is_drained_with_original_anchor(as_user, monkeypatch):
    from bot import handlers

    session.start(domain="ethics")
    session.set_question("Исходный вопрос", "ethics", q_num=12)
    session.enqueue_answer(
        "Ответ из очереди",
        message_id=77,
        source="answer",
    )
    captured = {}

    async def process(text, **kwargs):
        captured.update({"text": text, **kwargs})
        return None

    monkeypatch.setattr(handlers.conversation_service, "process_probe_answer", process)
    message = SimpleNamespace(from_user=SimpleNamespace(id=as_user))
    await handlers._drain_queued(message)
    assert captured["text"] == "Ответ из очереди"
    assert captured["question"] == "Исходный вопрос"
    assert captured["q_num"] == 12
    assert session.has_queued() is False


@pytest.mark.asyncio
async def test_recovery_skips_user_removed_from_whitelist(as_user, monkeypatch):
    current = session.start(domain="everyday")
    session.set_question("Что случилось?", "everyday", q_num=1)
    event = session_log.append_required(
        session_id=current.id,
        role="user",
        kind="answer",
        text="Ответ",
        q_num=1,
    )
    current.pending_answer = "Ответ"
    current.pending_answer_event_id = event["event_id"]
    session.persist()
    monkeypatch.setattr(recovery.users, "is_allowed", lambda _: False)

    class Bot:
        async def send_message(self, *args, **kwargs):
            raise AssertionError("removed user must not receive recovery")

    await recovery.process_pending_on_startup(Bot(), as_user)
    assert session.get().pending_answer_event_id == event["event_id"]
