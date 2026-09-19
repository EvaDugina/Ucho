from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from bot import about, llm, mood_file, moods, recovery, session, session_log, vault
from bot.errors import LLMError
from bot.services import about_service, conversation_service, mood_service

VALID = {"sign": "-", "energy": "low", "direction": "auto",
         "quality": "грусть_тоска", "dominance": "low"}


@pytest.mark.asyncio
@pytest.mark.parametrize("response", [{}, {"sign": "0"}, {**VALID, "quality": "unknown"},
                                      {"insufficient_data": True}, None])
async def test_classifier_does_not_invent_neutral_on_bad_response(monkeypatch, response):
    async def chat(*args, **kwargs):
        if response is None:
            raise LLMError("offline")
        return response

    monkeypatch.setattr(llm, "_chat_json", chat)
    with pytest.raises(LLMError):
        await llm.classify_mood("Мне грустно")


def _source(text="Мне грустно", *, sid="source", at="2026-05-20T21:00:00+03:00"):
    return session_log.append_required(session_id=sid, role="user", kind="note", text=text, at=at)


@pytest.mark.asyncio
async def test_about_uses_latest_raw_without_active_session_and_logs_each_call(as_user, monkeypatch):
    _source("Раньше было иначе", sid="z-old", at="2026-05-19T21:00:00+03:00")
    event = _source()
    session_log.append_required(session_id="commands", role="user", kind="command", text="/about")
    session_log.append_required(session_id="commands", role="user", kind="message", text="/unknown")
    assert session.get() is None
    captured = []

    async def classify(answer, portrait, session_context):
        captured.append(answer)
        assert "Мне грустно" in session_context
        assert "/about" not in session_context
        assert "Раньше было иначе" not in session_context
        return VALID

    monkeypatch.setattr(llm, "classify_mood", classify)
    at = datetime(2026, 9, 19, 13, tzinfo=timezone.utc)
    for _ in range(2):
        assert await mood_service.refresh_from_latest(at=at) == "updated"
    rows = [json.loads(line) for p in (vault.mood_dir() / "events").glob("*.jsonl")
            for line in p.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 2
    assert len({e["analysis_id"] for e in rows}) == 2
    assert all(e["raw_event_id"] == event["event_id"] and e["trigger"] == "about" for e in rows)
    assert all(e["ts"] == "2026-09-19T16:00:00+03:00" for e in rows)
    assert all(e["source_at"] == "2026-05-20T21:00:00+03:00" for e in rows)
    assert all(e["n"] == 1 and e["stability"] == "insufficient_data" for e in rows)
    assert captured == ["Мне грустно", "Мне грустно"]
    assert session.get() is None
    assert not moods.event_already_logged(event["event_id"])


@pytest.mark.asyncio
async def test_about_no_raw_does_not_create_mood_or_call_model(as_user, monkeypatch):
    async def fail(*args, **kwargs):
        pytest.fail("No source to classify")

    monkeypatch.setattr(llm, "classify_mood", fail)
    assert await mood_service.refresh_from_latest() == "no_data"
    assert not mood_file.path().exists()


@pytest.mark.asyncio
async def test_failed_mood_keeps_files_and_about_presentation(as_user, monkeypatch):
    _source()
    about.save_synthesis("### Манера речи\nКратко.", [])
    mood_file.set_current(moods.session_mood([VALID]))
    previous = mood_file.path().read_bytes()

    async def fail(*args, **kwargs):
        raise LLMError("offline")

    async def present(*args):
        return "Я вижу твой стиль."

    monkeypatch.setattr(llm, "classify_mood", fail)
    monkeypatch.setattr(llm, "about_present", present)
    spoken, profile, version = await about_service.refresh_and_present()
    assert "Я вижу твой стиль." in spoken
    assert "не удалось обновить" in spoken
    assert "Кратко." in profile and version is None
    assert mood_file.path().read_bytes() == previous
    assert not list((vault.mood_dir() / "events").glob("*.jsonl"))


@pytest.mark.asyncio
async def test_about_journal_failure_rolls_back_current(as_user, monkeypatch):
    _source()
    mood_file.set_current(moods.session_mood([VALID]))
    previous = mood_file.path().read_bytes()

    async def classify(*args, **kwargs):
        return {**VALID, "quality": "спокойствие"}

    monkeypatch.setattr(llm, "classify_mood", classify)
    monkeypatch.setattr(moods, "log_turn", lambda *args, **kwargs: False)
    assert await mood_service.refresh_from_latest() == "unavailable"
    assert mood_file.path().read_bytes() == previous


@pytest.mark.asyncio
@pytest.mark.parametrize("restart", [False, True])
async def test_failed_mood_does_not_block_answer_or_recovery(as_user, monkeypatch, restart):
    current = session.start(domain="everyday")
    session.set_question("Что случилось?", "everyday", q_num=1)
    mood_file.set_current(moods.session_mood([VALID]))
    previous = mood_file.path().read_bytes()

    async def fail(*args, **kwargs):
        raise LLMError("offline")

    async def process(*args, **kwargs):
        return {"reaction": "Я слышу тебя.", "personality_delta": []}

    target = recovery if restart else conversation_service
    monkeypatch.setattr(target, "classify_mood", fail)
    monkeypatch.setattr(target, "process_answer", process)
    if restart:
        event = _source(sid=current.id)
        current.pending_answer = event["text"]
        current.pending_answer_event_id = event["event_id"]
        session.persist()
        monkeypatch.setattr(recovery.users, "is_allowed", lambda _: True)

        async def send(*args, **kwargs):
            return SimpleNamespace(message_id=77, date=None)

        await recovery.process_pending_on_startup(SimpleNamespace(send_message=send), as_user)
    else:
        result = await conversation_service.process_probe_answer("Мне грустно")
        assert result.text == "Я слышу тебя."
    assert not session.has_pending(current)
    assert current.mood_trajectory == []
    assert mood_file.path().read_bytes() == previous
    assert not list((vault.mood_dir() / "events").glob("*.jsonl"))
