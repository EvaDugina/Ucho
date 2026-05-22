"""Юнит-тесты математики настроения (чистые функции, без vault)."""
from __future__ import annotations

from bot import moods


def test_normalize_per_msg_whitelist_fallback():
    out = moods.normalize_per_msg(
        {"sign": "xx", "energy": "high", "direction": "auto", "quality": "радость"}
    )
    assert out["sign"] == "0"        # невалидное → фолбэк
    assert out["energy"] == "high"   # валидное сохраняется
    assert out["direction"] == "auto"
    assert out["quality"] == "радость"


def test_normalize_per_msg_handles_non_dict():
    out = moods.normalize_per_msg(None)
    assert out["sign"] == "0"
    assert out["quality"] == "спокойствие"


def test_to_numeric():
    assert moods.to_numeric({"sign": "+", "energy": "high"}) == (1, 1)
    assert moods.to_numeric({"sign": "-", "energy": "low"}) == (-1, -1)
    assert moods.to_numeric({}) == (0, 0)


def test_session_mood_empty_uses_prior():
    mv = moods.session_mood([], prior=(0.5, -0.5))
    assert mv["n"] == 0
    assert mv["valence"] == 0.5
    assert mv["arousal"] == -0.5


def test_session_mood_clamps_and_reports():
    traj = [{"sign": "-", "energy": "low"}, {"sign": "+", "energy": "high"}]
    mv = moods.session_mood(traj)
    assert mv["n"] == 2
    assert -1.0 <= mv["valence"] <= 1.0
    assert -1.0 <= mv["arousal"] <= 1.0
    assert mv["sign"] in moods.SIGNS
    assert mv["stability"] in ("rigid", "adequate", "labile")
