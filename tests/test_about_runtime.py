from __future__ import annotations

from datetime import datetime

import pytest

from bot import about
from bot.errors import LLMError
from bot.services import about_service


def _record():
    return about.record_deltas(
        [
            {
                "aspect": "character",
                "summary": "Последователен в формулировках.",
                "quote": "я довожу дело",
                "confidence": 0.7,
            },
            {
                "aspect": "values",
                "summary": "Недоказанная дельта.",
                "quote": "этого в сыром тексте нет",
                "confidence": 1,
            },
        ],
        raw_event_id="s:000001",
        raw_text="Я сказал: я довожу дело до конца.",
    )


def test_delta_validation_and_ids(as_user):
    accepted = _record()
    assert len(accepted) == 1
    assert accepted[0]["id"]
    assert accepted[0]["raw_event_id"] == "s:000001"
    assert accepted[0]["status"] == "pending"


@pytest.mark.asyncio
async def test_about_synthesizes_then_presents_and_marks_atomically(as_user, monkeypatch):
    item = _record()[0]
    calls = []

    async def synthesize(current, pending):
        calls.append(("synthesize", current, pending[0]["id"]))
        return "# Внутренний профиль\n\nПоследователен."

    async def present(profile):
        calls.append(("present", profile))
        return "Я вижу в тебе последовательность."

    monkeypatch.setattr(about_service.llm, "synthesize_about", synthesize)
    monkeypatch.setattr(about_service.llm, "about_present", present)
    spoken, profile, version = await about_service.refresh_and_present(
        at=datetime(2026, 7, 27, 12, 30, 0)
    )
    assert spoken.startswith("Я вижу")
    assert profile == "# Внутренний профиль\n\nПоследователен."
    assert version == "2026-07-27_12-30-00"
    assert [call[0] for call in calls] == ["synthesize", "present"]
    assert not about.pending_deltas()
    store = about._load_store()
    saved = next(row for row in store["items"] if row["id"] == item["id"])
    assert saved["status"] == "synthesized"
    assert saved["synthesized_in"] == version
    assert (about.versions_dir() / f"{version}.md").exists()


@pytest.mark.asyncio
async def test_about_failure_does_not_mark_or_create_version(as_user, monkeypatch):
    item = _record()[0]

    async def fail(*args, **kwargs):
        raise LLMError("failed")

    monkeypatch.setattr(about_service.llm, "synthesize_about", fail)
    with pytest.raises(LLMError):
        await about_service.refresh_and_present()
    assert about.pending_deltas()[0]["id"] == item["id"]
    assert list(about.versions_dir().glob("*.md")) == []


def test_about_file_failure_restores_profile_and_pending_deltas(as_user, monkeypatch):
    _record()
    before = about.deltas_path().read_bytes()

    def fail_json(*_args, **_kwargs):
        raise OSError("disk error")

    monkeypatch.setattr(about, "atomic_write_json", fail_json)
    with pytest.raises(OSError, match="disk error"):
        about.save_synthesis(
            "# Профиль\n\nНовый текст.",
            [item["id"] for item in about.pending_deltas()],
            at=datetime(2026, 9, 19, 10, 0, 0),
        )
    assert about.deltas_path().read_bytes() == before
    assert not about.path().exists()
    assert list(about.versions_dir().glob("*.md")) == []
