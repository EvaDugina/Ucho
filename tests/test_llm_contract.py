"""Юнит-тесты валидации контракта worldview_observations (pydantic) — без сети.

normalize_worldview_observations отсеивает мусорный JSON от LLM до сервис-слоя.
"""
from __future__ import annotations

from bot.config import LOG_PATH
from bot.llm import normalize_observations, normalize_worldview_observations


def test_keeps_valid_and_defaults_type():
    out = normalize_worldview_observations(
        [{"name": "Честность", "area": "values_norms", "category": "norms", "theme": "честность", "summary": "о доверии", "quote": "правда"}]
    )
    assert len(out) == 1
    o = out[0]
    assert o["name"] == "Честность"
    assert o["type"] == "claim"  # дефолт
    assert o["area"] == "values_norms"
    assert o["category"] == "norms"
    assert o["theme"] == "честность"


def test_drops_non_dict_and_nameless():
    out = normalize_worldview_observations(
        ["мусор", 42, None, {}, {"summary": "без имени"}, {"name": "   "}]
    )
    assert out == []


def test_ignores_extra_fields():
    out = normalize_worldview_observations([{"name": "X", "лишнее": "поле", "weight": 9}])
    assert len(out) == 1
    assert "лишнее" not in out[0]
    assert set(out[0]) == {"name", "area", "category", "theme", "type", "summary", "quote", "confidence"}


def test_handles_none_and_empty():
    assert normalize_worldview_observations(None) == []
    assert normalize_worldview_observations([]) == []


def test_dropped_observations_logged(as_user):
    # Отброшенные наблюдения (потеря данных пользователя) должны оставлять след
    # в .psycho/log.md, а не тонуть только в stderr.
    before = LOG_PATH.read_text(encoding="utf-8") if LOG_PATH.exists() else ""
    out = normalize_worldview_observations(["мусор", {"name": "Ок", "area": "values_norms", "category": "norms", "theme": "честность"}])
    assert len(out) == 1
    after = LOG_PATH.read_text(encoding="utf-8")
    assert "process_worldview_observations_dropped" in after
    assert len(after) > len(before)


def test_legacy_observations_are_mapped_to_worldview():
    out = normalize_observations([{"name": "Слово", "domain": "ethics"}])
    assert out[0]["area"] == "values_norms"
    assert out[0]["category"] == "norms"
