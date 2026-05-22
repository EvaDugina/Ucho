"""Юнит-тесты русского VAD-лексикона (bot/lexicon.py)."""
from __future__ import annotations

import pytest

from bot import lexicon

_HAS_LEXICON = bool(lexicon._load_lexicon())


@pytest.mark.skipif(not _HAS_LEXICON, reason="нет bot/data/nrc_vad_ru.tsv")
def test_known_word_returns_vector():
    # «радость» — высокая валентность; вектор в [-1..1].
    out = lexicon.score_sync("радость")
    assert out is not None
    assert out["valence"] > 0.5
    for k in ("valence", "arousal", "dominance"):
        assert -1.0 <= out[k] <= 1.0
    assert out["n"] >= 1


@pytest.mark.skipif(not _HAS_LEXICON, reason="нет bot/data/nrc_vad_ru.tsv")
def test_negative_word_low_valence():
    out = lexicon.score_sync("грусть")
    assert out is not None
    assert out["valence"] < 0.0


def test_empty_and_garbage_return_none():
    assert lexicon.score_sync("") is None
    assert lexicon.score_sync("   ") is None
    # Несловарный мусор (латиница вне лексикона) → None.
    assert lexicon.score_sync("zzzqqq wwwxxx") is None


def test_rescale_bounds():
    assert lexicon._rescale(0.0) == -1.0
    assert lexicon._rescale(1.0) == 1.0
    assert lexicon._rescale(0.5) == 0.0
