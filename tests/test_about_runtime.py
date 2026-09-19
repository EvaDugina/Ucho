from __future__ import annotations

from datetime import datetime, timezone

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


def test_new_metadata_fields_do_not_rewrite_body_or_evidence(as_user):
    item = _record()[0]
    before = about.deltas_path().read_bytes()
    current = (
        '---\nРегистр речи: "разговорный"\n'
        'Предпочтительная подробность ответов: "подробно"\n'
        'Предпочтительный темп диалога: "вдумчивый"\n'
        'Отношение к прямым вопросам: "недостаточно данных"\n'
        '---\n\n### Манера речи\nПрежний текст.'
    )
    about.save_synthesis(current, [])
    updated = about.current_profile()
    assert updated.startswith(
        '---\nРегистр речи: "разговорный"\nТон речи: "..."\n'
        'Открытость: "..."\nПереносимость провокаций: "..."\n'
        'Ход мысли: "..."\nОтношение к прямым вопросам: "..."\n---\n'
    )
    assert "Предпочтительная подробность ответов" not in updated
    assert "Предпочтительный темп диалога" not in updated
    assert "недостаточно данных" not in updated
    assert 'Регистр речи: "разговорный"' in updated
    assert about.profile_body(updated) == about.profile_body(current)
    assert about.has_profile_metadata(updated)
    assert about.deltas_path().read_bytes() == before
    assert about.pending_deltas()[0]["id"] == item["id"]


@pytest.mark.parametrize("raw", ['null', '"недостаточно данных"', '"..."', '"…"', '""'])
def test_unknown_metadata_uses_three_dots(raw):
    profile = about.localize_profile_metadata(f'---\nthought_flow: {raw}\n---\n\nОписание.')
    assert profile == '---\nХод мысли: "..."\n---\n\nОписание.'


@pytest.mark.parametrize("counter", ["messages_seen: 1", 'Учтено сообщений: "недостаточно данных"'])
def test_removed_message_counter_is_not_saved(as_user, counter):
    item = _record()[0]
    about.save_synthesis(f"---\n{counter}\n---\n\n### Манера речи\nОписание.", [item["id"]])
    profile = about.current_profile()
    assert "Учтено сообщений:" not in profile
    assert "messages_seen:" not in profile
    assert about.profile_body(profile) == "### Манера речи\nОписание."


@pytest.mark.asyncio
async def test_about_synthesizes_then_presents_and_marks_atomically(as_user, monkeypatch):
    item = _record()[0]
    calls = []

    async def synthesize(current, pending):
        calls.append(("synthesize", current, pending[0]["id"]))
        return "```markdown\n# Внутренний профиль\n\nПоследователен.\n```"

    async def present(profile):
        calls.append(("present", profile))
        return "Я вижу в тебе последовательность."

    monkeypatch.setattr(about_service.llm, "synthesize_about", synthesize)
    monkeypatch.setattr(about_service.llm, "about_present", present)
    spoken, profile, version = await about_service.refresh_and_present(
        at=datetime(2026, 7, 27, 12, 30, 0)
    )
    assert spoken.startswith("Я вижу")
    assert about.profile_body(profile) == "# Внутренний профиль\n\nПоследователен."
    assert about.has_profile_metadata(profile)
    assert "Учтено сообщений:" not in profile
    assert "updated:" not in profile
    assert version == "2026-07-27_12-30-00"
    assert [call[0] for call in calls] == ["synthesize", "present"]
    assert not about.pending_deltas()
    store = about._load_store()
    saved = next(row for row in store["items"] if row["id"] == item["id"])
    assert saved["status"] == "synthesized"
    assert saved["synthesized_in"] == version
    assert (about.versions_dir() / f"{version}.md").exists()
    assert about.path().read_text(encoding="utf-8").strip() == profile
    assert (about.versions_dir() / f"{version}.md").read_text(encoding="utf-8").strip() == profile


@pytest.mark.asyncio
async def test_existing_fenced_profile_is_presented_without_new_version(as_user, monkeypatch):
    profile = (
        "---\nupdated: '2026-09-19'\nmessages_seen: 1\nregister: книжный\n"
        "tone: спокойный\nopenness: 4/5\nprovocation_tolerance: null\n---\n\n"
        "### Манера речи\nСтарый профиль."
    )
    original = f"```markdown\n{profile}\n```\n"
    about.path().write_text(original, encoding="utf-8")

    async def present(value):
        assert value == about.localize_profile_metadata(profile)
        return "Я вижу твой стиль."

    monkeypatch.setattr(about_service.llm, "about_present", present)
    spoken, returned, version = await about_service.refresh_and_present()
    assert spoken == "Я вижу твой стиль."
    assert returned == about.localize_profile_metadata(profile)
    assert version is None
    assert about.path().read_text(encoding="utf-8") == original


@pytest.mark.asyncio
async def test_no_pending_does_not_rewrite_profile_or_evidence(as_user, monkeypatch):
    _record()
    _record()  # Две дельты одного raw-сообщения — всё ещё одно сообщение.
    store = about._load_store()
    for item in store["items"]:
        item["status"] = "synthesized"
    about.atomic_write_json(about.deltas_path(), store)
    before_deltas = about.deltas_path().read_bytes()
    original = "```markdown\n### Манера речи\nПрежний подробный текст.\n```"
    about.path().write_text(original, encoding="utf-8")
    calls = []

    async def chat(task, messages, temperature):
        calls.append(task)
        return {"register": "книжный", "tone": "спокойный", "openness": 4,
                "provocation_tolerance": None, "profile": "Не подменять исходный текст."}

    async def present(profile):
        return "Я вижу твой стиль."

    monkeypatch.setattr(about_service.llm, "_chat_json", chat)
    monkeypatch.setattr(about_service.llm, "about_present", present)
    at = datetime(2026, 9, 19, 22, 30, tzinfo=timezone.utc)
    _, profile, version = await about_service.refresh_and_present(at=at)
    assert version is None
    assert about.profile_body(profile) == "### Манера речи\nПрежний подробный текст."
    assert "updated:" not in profile
    assert "Учтено сообщений:" not in profile
    assert about.deltas_path().read_bytes() == before_deltas
    _, repeated, second_version = await about_service.refresh_and_present(at=at)
    assert repeated == profile
    assert second_version is None
    assert calls == []


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
