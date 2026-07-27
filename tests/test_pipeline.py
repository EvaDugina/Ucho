from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from bot import about, recovery, session, session_log, userctx
from bot.errors import LLMError
from bot.services import conversation_service


def _mood():
    return {
        "sign": "-",
        "energy": "high",
        "direction": "auto",
        "quality": "тревога",
        "dominance": "low",
    }


@pytest.mark.asyncio
async def test_raw_before_llm_and_mood_personality_outputs(as_user, monkeypatch):
    current = session.start(mode="probe", domain="ethics")
    session.set_question("Что для тебя честность?", "ethics", q_num=1)
    observed = {}

    async def classify(*args, **kwargs):
        return _mood()

    async def process(*args, **kwargs):
        events = session_log.session_events(current.id)
        observed["raw_seen"] = events[-1]["kind"] == "answer"
        observed["event_id"] = events[-1]["event_id"]
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
            "mask_frequency_draft": {"сомнение": 0.4},
        }

    monkeypatch.setattr(conversation_service, "classify_mood", classify)
    monkeypatch.setattr(conversation_service, "process_answer", process)
    payload = await conversation_service.process_probe_answer(
        "Для меня честность выше удобства.",
        message_id=101,
    )
    assert payload is not None
    assert observed["raw_seen"] is True
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
    current = session.start(mode="probe", domain="everyday")
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


@pytest.mark.asyncio
async def test_recovery_processes_existing_event_without_duplicate(as_user, monkeypatch):
    current = session.start(mode="probe", domain="everyday")
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
            "mask_frequency_draft": {},
        }

    class BotStub:
        async def send_message(self, *args, **kwargs):
            return SimpleNamespace(message_id=77, date=None)

    monkeypatch.setattr(recovery, "classify_mood", classify)
    monkeypatch.setattr(recovery, "process_answer", process)
    await recovery.process_pending_on_startup(BotStub(), as_user)
    user_events = [
        item for item in session_log.session_events(current.id) if item["role"] == "user"
    ]
    assert len(user_events) == 1
    assert session.get().pending_answer_event_id is None
    assert about.pending_deltas()[0]["raw_event_id"] == event["event_id"]


@pytest.mark.asyncio
async def test_durable_queue_is_drained_with_original_anchor(as_user, monkeypatch):
    from bot import handlers

    session.start(mode="probe", domain="ethics")
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
