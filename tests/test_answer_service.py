"""Юнит-тесты сервис-слоя записи в граф (вынесено из handlers).

apply_processed принимает готовый ``result``-dict (как от llm.process_answer) —
поэтому тестируется без Telegram/live-LLM provider, на изолированном tmp-вольте.
"""
from __future__ import annotations

from datetime import datetime

from bot import graph
from bot.services.answer_service import apply_processed


def test_apply_processed_creates_draft(as_user):
    result = {
        "observations": [
            {
                "name": "Честность",
                "domain": "ethics",
                "type": "value",
                "summary": "Честность это основа доверия между людьми во всех делах",
                "quote": "я всегда говорю правду",
            }
        ],
        "reaction": "складно",
        "user_delta": {},
    }
    created, updated = apply_processed(
        result,
        q_num=1,
        asked_at=datetime.now(),
        original_question="Важна ли честность?",
        original_answer="я всегда говорю правду людям",
        session_domain="ethics",
    )
    assert (created, updated) == (1, 0)
    c = graph.load_concept("chestnost", "ethics")
    assert c is not None
    assert c.status == "draft"
    assert c.domain == "ethics"


def test_apply_processed_dedup_updates_not_creates(as_user):
    summary = "Свобода это способность выбирать свой путь без принуждения извне"
    first = {
        "observations": [
            {"name": "Свобода", "domain": "politics", "type": "value", "summary": summary, "quote": "хочу сам решать"}
        ],
        "user_delta": {},
    }
    apply_processed(
        first, 1, datetime.now(), "Q", "хочу сам решать всё в жизни", session_domain="politics"
    )
    # Тот же концепт по имени → дедуп → update (новая evidence), не второй файл.
    again = {
        "observations": [
            {"name": "Свобода", "domain": "politics", "type": "value", "summary": summary, "quote": "никто мне не указ"}
        ],
        "user_delta": {},
    }
    created, updated = apply_processed(
        again, 2, datetime.now(), "Q", "никто мне не указ в моих делах", session_domain="politics"
    )
    assert (created, updated) == (0, 1)
