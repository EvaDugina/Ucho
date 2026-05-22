"""Юнит-тесты валидации контракта observations (pydantic) — без сети.

normalize_observations отсеивает мусорный JSON от 14B-модели до сервис-слоя.
"""
from __future__ import annotations

from bot.llm import normalize_observations


def test_keeps_valid_and_defaults_type():
    out = normalize_observations(
        [{"name": "Честность", "domain": "ethics", "summary": "о доверии", "quote": "правда"}]
    )
    assert len(out) == 1
    o = out[0]
    assert o["name"] == "Честность"
    assert o["type"] == "claim"  # дефолт
    assert o["domain"] == "ethics"


def test_drops_non_dict_and_nameless():
    out = normalize_observations(
        ["мусор", 42, None, {}, {"summary": "без имени"}, {"name": "   "}]
    )
    assert out == []


def test_ignores_extra_fields():
    out = normalize_observations([{"name": "X", "лишнее": "поле", "weight": 9}])
    assert len(out) == 1
    assert "лишнее" not in out[0]
    assert set(out[0]) == {"name", "domain", "type", "summary", "quote"}


def test_handles_none_and_empty():
    assert normalize_observations(None) == []
    assert normalize_observations([]) == []
