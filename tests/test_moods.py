"""Юнит-тесты математики настроения (чистые функции, без vault)."""
from __future__ import annotations

from bot import moods


def test_normalize_per_msg_whitelist_fallback():
    out = moods.normalize_per_msg(
        {"sign": "xx", "energy": "high", "direction": "auto",
         "quality": "радость", "dominance": "zz"}
    )
    assert out["sign"] == "0"          # невалидное → фолбэк
    assert out["energy"] == "high"     # валидное сохраняется
    assert out["direction"] == "auto"
    assert out["quality"] == "радость"
    assert out["dominance"] == "normal"  # невалидное → фолбэк


def test_normalize_per_msg_handles_non_dict():
    out = moods.normalize_per_msg(None)
    assert out["sign"] == "0"
    assert out["quality"] == "спокойствие"
    assert out["dominance"] == "normal"


def test_to_numeric():
    assert moods.to_numeric({"sign": "+", "energy": "high", "dominance": "high"}) == (1, 1, 1)
    assert moods.to_numeric({"sign": "-", "energy": "low", "dominance": "low"}) == (-1, -1, -1)
    assert moods.to_numeric({}) == (0, 0, 0)


def test_session_mood_empty_uses_prior():
    mv = moods.session_mood([], prior=(0.5, -0.5, 0.7))
    assert mv["n"] == 0
    assert mv["valence"] == 0.5
    assert mv["arousal"] == -0.5
    assert mv["dominance"] == 0.7
    assert mv["dominance_label"] == "high"


def test_session_mood_accepts_legacy_two_prior():
    # Старый 2-элементный prior (back-compat) → dominance=0.0.
    mv = moods.session_mood([], prior=(0.4, 0.4))
    assert mv["dominance"] == 0.0
    assert mv["dominance_label"] == "normal"


def test_session_mood_clamps_and_reports():
    traj = [
        {"sign": "-", "energy": "low", "dominance": "low"},
        {"sign": "+", "energy": "high", "dominance": "high"},
    ]
    mv = moods.session_mood(traj)
    assert mv["n"] == 2
    assert -1.0 <= mv["valence"] <= 1.0
    assert -1.0 <= mv["arousal"] <= 1.0
    assert -1.0 <= mv["dominance"] <= 1.0
    assert mv["sign"] in moods.SIGNS
    assert mv["dominance_label"] in moods.DOMINANCE
    assert mv["stability"] in ("rigid", "adequate", "labile")


def test_pick_bot_mood_low_dominance_supports():
    # Придавлен/бессилен + минус → поддерживающее лицо (контраст-политика D).
    mv = {"valence": -0.6, "arousal": -0.3, "dominance_label": "low",
          "energy": "low", "quality": "грусть_тоска", "direction": "auto"}
    face = moods.pick_bot_mood(mv)
    assert face in {"вселение_уверенности", "вера", "ласка", "клятва"}


def test_pick_bot_mood_high_dominance_cuts_down():
    # Властен/высокомерен + плюс → осаживающее лицо.
    mv = {"valence": 0.6, "arousal": 0.5, "dominance_label": "high",
          "energy": "high", "quality": "гордость_самоуверенность", "direction": "hetero"}
    face = moods.pick_bot_mood(mv)
    assert face in {"сомнение", "холодная_отстранённость", "насмешка", "давление_на_больное"}


def test_mood_label_includes_dominance():
    mv = moods.session_mood(
        [{"sign": "-", "energy": "low", "dominance": "low", "quality": "грусть_тоска"}]
    )
    assert "dom:" in moods.mood_label(mv)
