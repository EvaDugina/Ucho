"""Юнит-тесты сервис-слоя записи в граф (вынесено из handlers).

apply_processed принимает готовый ``result``-dict (как от llm.process_answer) —
поэтому тестируется без Telegram/live-LLM provider, на изолированном tmp-вольте.
"""
from __future__ import annotations

from datetime import datetime

from bot import worldview
from bot.services.answer_service import apply_processed


def test_apply_processed_creates_draft(as_user):
    result = {
        "worldview_observations": [
            {
                "name": "Честность",
                "area": "values_norms",
                "category": "norms",
                "theme": "честность",
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
        target={"area": "values_norms", "category": "norms", "theme": "честность"},
    )
    assert (created, updated) == (1, 0)
    c = worldview.load_atom("chestnost", "values_norms")
    assert c is not None
    assert c.status == "draft"
    assert c.area == "values_norms"


def test_apply_processed_dedup_updates_not_creates(as_user):
    summary = "Свобода это способность выбирать свой путь без принуждения извне"
    first = {
        "worldview_observations": [
            {"name": "Свобода", "area": "values_norms", "category": "values", "theme": "свобода", "type": "value", "summary": summary, "quote": "хочу сам решать"}
        ],
        "user_delta": {},
    }
    apply_processed(
        first, 1, datetime.now(), "Q", "хочу сам решать всё в жизни",
        target={"area": "values_norms", "category": "values", "theme": "свобода"},
    )
    # Тот же концепт по имени → дедуп → update (новая evidence), не второй файл.
    again = {
        "worldview_observations": [
            {"name": "Свобода", "area": "values_norms", "category": "values", "theme": "свобода", "type": "value", "summary": summary, "quote": "никто мне не указ"}
        ],
        "user_delta": {},
    }
    created, updated = apply_processed(
        again, 2, datetime.now(), "Q", "никто мне не указ в моих делах",
        target={"area": "values_norms", "category": "values", "theme": "свобода"},
    )
    assert (created, updated) == (0, 1)
