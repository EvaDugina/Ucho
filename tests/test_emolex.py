"""Юнит-тесты эмо-лексикона NRC-EmoLex (bot/emolex.py)."""
from __future__ import annotations

import pytest

from bot import emolex

_HAS = bool(emolex._load())


@pytest.mark.skipif(not _HAS, reason="нет bot/data/nrc_emolex_ru.tsv")
def test_emotional_words_return_emotions():
    out = emolex.score_sync("страх и гнев")
    assert out is not None
    assert out["n"] >= 1
    assert any(out[e] > 0 for e in emolex.EMOTIONS)
    # доли ∈ [0..1]
    for e in emolex.EMOTIONS:
        assert 0.0 <= out[e] <= 1.0
    assert isinstance(out["top"], list)


@pytest.mark.skipif(not _HAS, reason="нет bot/data/nrc_emolex_ru.tsv")
def test_positive_word():
    out = emolex.score_sync("радость")
    assert out is not None
    assert out["positive"] > 0.0


def test_empty_and_garbage_return_none():
    assert emolex.score_sync("") is None
    assert emolex.score_sync("   ") is None
    assert emolex.score_sync("zzzqqq wwwxxx") is None
