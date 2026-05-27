"""Юнит-тесты математики настроения (чистые функции, без vault)."""
from __future__ import annotations

from bot import moods, userctx, vault
from bot.atomic import atomic_write_json


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


def test_curated_mask_frequencies_are_read_only_and_default_zero(as_user):
    freqs = moods.load_curated_mask_frequencies()
    path = userctx.user_root() / "03_personality" / "mask_frequencies.json"

    assert set(freqs) == set(moods.BOT_MOODS)
    assert all(v == 0.0 for v in freqs.values())
    assert not path.exists()

    userctx.set_user(as_user + 100_000)
    vault.ensure_layout()
    other_path = userctx.user_root() / "03_personality" / "mask_frequencies.json"
    other_freqs = moods.load_curated_mask_frequencies()

    assert other_path != path
    assert not other_path.exists()
    assert set(other_freqs) == set(moods.BOT_MOODS)
    assert all(v == 0.0 for v in other_freqs.values())


def test_mask_frequency_draft_is_per_user_and_complete(as_user):
    curated_path = userctx.user_root() / "03_personality" / "mask_frequencies.json"
    draft_path = userctx.user_root() / "03_personality" / "mask_frequencies_draft.json"

    draft = moods.record_mask_frequency_draft(
        {"постирония": 0.42, "сомнение": "bad", "не_маска": 1},
        bot_mood="постирония",
        at="2026-05-27T10:00:00",
    )

    assert not curated_path.exists()
    assert draft_path.exists()
    assert set(draft["coefficients"]) == set(moods.BOT_MOODS)
    assert draft["coefficients"]["постирония"] == 0.42
    assert draft["coefficients"]["сомнение"] == 0.0
    assert draft["answer_count"] == 1

    userctx.set_user(as_user + 100_000)
    vault.ensure_layout()
    other_draft = moods.load_mask_frequency_draft()

    assert not (userctx.user_root() / "03_personality" / "mask_frequencies_draft.json").exists()
    assert set(other_draft["coefficients"]) == set(moods.BOT_MOODS)
    assert all(v == 0.0 for v in other_draft["coefficients"].values())


def test_weighted_bot_mood_respects_curated_and_draft(as_user):
    path = userctx.user_root() / "03_personality" / "mask_frequencies.json"
    freqs = {m: 0.0 for m in moods.BOT_MOODS}
    freqs["сомнение"] = 1.0
    atomic_write_json(path, freqs)

    assert moods.weighted_bot_mood(["ласка", "сомнение", "постирония"]) == "сомнение"

    moods.record_mask_frequency_draft({"ласка": 1.0}, bot_mood="ласка")

    assert moods.weighted_bot_mood(["ласка", "постирония"]) == "ласка"


def test_record_mask_like_follows_curve_and_never_reaches_one(as_user):
    c0 = moods.mask_like_coefficient(0)
    c1 = moods.mask_like_coefficient(1)
    c10 = moods.mask_like_coefficient(10)
    c_big = moods.mask_like_coefficient(1_000_000)

    assert c0 == 0.0
    assert 0.25 < c1 < 0.6
    assert c1 < c10 < c_big < 1.0

    for _ in range(3):
        draft = moods.record_mask_like("вера", at="2026-05-27T10:00:00")

    assert draft["like_counts"]["вера"] == 3
    assert draft["coefficients"]["вера"] == moods.mask_like_coefficient(3)
    assert draft["coefficients"]["вера"] < 1.0


def test_mood_label_includes_dominance():
    mv = moods.session_mood(
        [{"sign": "-", "energy": "low", "dominance": "low", "quality": "грусть_тоска"}]
    )
    assert "dom:" in moods.mood_label(mv)
