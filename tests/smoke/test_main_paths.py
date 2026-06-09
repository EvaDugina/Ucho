from __future__ import annotations

from datetime import datetime

import pytest

from bot import moods, session, session_log, userctx, worldview
from bot.services import conversation_service, note_service


def _fake_result(reaction: str = "Слышу, где слово скрипит.") -> dict:
    return {
        "worldview_observations": [
            {
                "area": "values_norms",
                "category": "norms",
                "theme": "честность",
                "type": "value",
                "name": "Честность",
                "summary": "Честность важна даже когда неудобна.",
                "quote": "честность важна",
            }
        ],
        "reaction": reaction,
        "user_delta": {},
        "mask_frequency_draft": {"постирония": 0.11},
    }


@pytest.mark.asyncio
async def test_smoke_ucho_note_is_durable_and_returns_reaction_payload(as_user, monkeypatch):
    async def fake_process_answer(**kwargs):
        return _fake_result("Записал не как отчёт, а как занозу.")

    monkeypatch.setattr(note_service, "process_answer", fake_process_answer)

    payload = await note_service.ingest_note(
        "честность важна даже когда неудобна",
        at=datetime(2026, 5, 26, 12, 0),
    )

    note_files = list((userctx.user_root() / "00_raw" / "notes").glob("*.md"))
    note_text = "\n".join(p.read_text(encoding="utf-8") for p in note_files)
    assert "честность важна даже когда неудобна" in note_text
    assert payload is not None
    assert "Записал не как отчёт" in payload.text
    assert "Заметка сохранена" not in payload.text
    assert "+created" not in payload.text
    assert moods.load_mask_frequency_draft()["coefficients"]["постирония"] == 0.11


@pytest.mark.asyncio
async def test_smoke_answer_logs_before_llm_creates_draft_and_clears_pending(as_user, monkeypatch):
    target = {"area": "values_norms", "category": "norms", "theme": "честность"}
    s = session.start(mode="probe", target=target)
    session.set_question("Что для тебя честность?", target=target, q_num=1)

    async def fake_process_answer(**kwargs):
        events = session_log.session_events(s.id)
        assert any(e.get("role") == "user" and e.get("kind") == "answer" for e in events)
        return _fake_result()

    monkeypatch.setattr(conversation_service, "process_answer", fake_process_answer)

    payload = await conversation_service.process_probe_answer(
        "честность важна даже когда неудобна",
        message_id=777,
        at=datetime(2026, 5, 26, 12, 5),
        is_owner=False,
    )

    assert payload is not None
    draft = moods.load_mask_frequency_draft()
    assert draft["coefficients"]["постирония"] == 0.11
    assert draft["answer_count"] == 1
    assert session.get().pending_answer_event_id is None
    slug = worldview.resolve_slug("Честность")
    assert slug == "chestnost"
    atom_path = userctx.user_root() / "03_Ценностно-нормативная подсистема" / "atoms" / "chestnost.md"
    atom_text = atom_path.read_text(encoding="utf-8")
    assert "status: draft" in atom_text
    assert "честность важна" in atom_text


def test_smoke_recovery_reads_pending_text_from_session_log(as_user):
    target = {"area": "values_norms", "category": "norms", "theme": "честность"}
    s = session.start(mode="probe", target=target)
    session.set_question("Что для тебя честность?", target=target, q_num=1)
    event = session_log.append_required(
        session_id=s.id,
        role="user",
        kind="answer",
        text="честность важна даже когда неудобна",
        at=datetime(2026, 5, 26, 12, 10),
        message_id=778,
        q_num=1,
        area="values_norms",
        category="norms",
        theme="честность",
        theme_key="values_norms/norms/честность",
    )
    s.pending_answer_event_id = event["event_id"]
    session.persist()

    assert session.has_pending(s)
    assert session.pending_answer_text(s) == "честность важна даже когда неудобна"
    assert session_log.find_event(event["event_id"])["text"] == "честность важна даже когда неудобна"
