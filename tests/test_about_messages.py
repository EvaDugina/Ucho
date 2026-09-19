from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import pytest

from bot import handlers
from bot.services.about_messages import format_full_profile
from bot.services.session_messages import TG_MSG_LIMIT


def test_full_profile_formats_frontmatter_and_headings_safely():
    profile = (
        "---\nupdated: '2026-09-19'\nmessages_seen: 13\nprovocation_tolerance: high\n"
        "register: книжный\ntone: null\nopenness: 4/5\n"
        "thought_flow: Сопоставляет примеры.\n"
        "preferred_response_detail: detailed\ndirect_questions_attitude: welcomes\n"
        "preferred_dialogue_pace: context_dependent\n---\n\n"
        "### Манера <речи>\nЯ говорю & слушаю.\n\n"
        "### Ценности\n- Бережно отношусь к словам."
    )
    chunks = format_full_profile(profile)
    assert len(chunks) == 1
    body = chunks[0]
    assert body.startswith("<b>Полная версия описания</b>")
    assert "<pre>---\nРегистр речи: &quot;книжный&quot;\nТон речи: &quot;...&quot;" in body
    assert "Отношение к прямым вопросам: &quot;принимает прямые вопросы&quot;" in body
    assert "Переносимость провокаций: &quot;высокая&quot;\nХод мысли: &quot;Сопоставляет примеры.&quot;\n" in body
    assert "Отношение к прямым вопросам: &quot;принимает прямые вопросы&quot;\n---</pre>" in body
    assert "Предпочтительная подробность ответов" not in body
    assert "Предпочтительный темп диалога" not in body
    assert "updated:" not in body
    assert "messages_seen:" not in body
    assert "Учтено сообщений:" not in body
    assert "<b>Манера &lt;речи&gt;</b>" in body
    assert "Я говорю &amp; слушаю." in body
    assert "<b>Ценности</b>" in body
    assert "• Бережно отношусь к словам." in body


def test_long_profile_splits_without_losing_text_or_breaking_html():
    profile = "### Манера речи\n" + "Слово & " * 1200
    chunks = format_full_profile(profile)
    assert len(chunks) > 1
    assert all(len(chunk) <= TG_MSG_LIMIT for chunk in chunks)
    assert sum(chunk.count("Слово") for chunk in chunks) == 1200
    assert sum(chunk.count("&amp;") for chunk in chunks) == 1200
    assert all(chunk.count("<b>") == chunk.count("</b>") for chunk in chunks)
    assert all(chunk.count("<pre>") == chunk.count("</pre>") for chunk in chunks)


def test_fenced_profile_still_formats_metadata_and_headings():
    profile = "---\nupdated: '2026-09-19'\n---\n\n### Манера речи\nТекст."
    assert format_full_profile(f"```markdown\n{profile}\n```") == format_full_profile(profile)


@pytest.mark.asyncio
async def test_about_sends_full_profile_after_spoken_summary(as_user, monkeypatch):
    sent = []

    async def refresh(*, at):
        return "Я вижу главное.", "### Манера речи\nПолное описание.", None

    async def answer(text, **kwargs):
        sent.append((text, kwargs))

    monkeypatch.setattr(handlers.about_service, "refresh_and_present", refresh)
    message = SimpleNamespace(date=datetime(2026, 9, 19), answer=answer)
    await handlers.cmd_about(message)

    assert len(sent) == 2
    assert sent[0] == ("Я вижу главное.", {"parse_mode": "HTML"})
    assert sent[1][0].startswith("<b>Полная версия описания</b>")
    assert "<b>Манера речи</b>" in sent[1][0]
    assert "Полное описание." in sent[1][0]
    assert sent[1][1] == {"parse_mode": "HTML"}
