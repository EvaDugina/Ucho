"""Юнит-тесты форматирования отчёта сравнения методов (bot/analysis.py)."""
from __future__ import annotations

from bot import analysis


def test_format_report_handles_all_none():
    results = {k: None for k in ("pad", "vad_lex", "emolex", "dostoevsky", "ocean", "panas")}
    s = analysis.format_report(None, None, results)
    assert "Анализ ответа" in s
    # каждый метод деградирует до плейсхолдера, а не падает
    assert "нет" in s.lower()
    assert "Big Five" in s and "PANAS" in s


def test_format_report_full():
    results = {
        "pad": {"quality": "грусть_тоска", "valence": -0.5, "arousal": -0.5,
                "dominance": -0.5, "dominance_label": "low", "stability": "rigid"},
        "vad_lex": {"valence": -0.4, "arousal": -0.1, "dominance": -0.3, "n": 2},
        "emolex": {"top": ["sadness", "fear"], "sadness": 0.5, "fear": 0.3,
                   "positive": 0.0, "negative": 0.5, "n": 3},
        "dostoevsky": {"label": "negative", "score": 0.8},
        "ocean": {"openness": 0.6, "conscientiousness": 0.5, "extraversion": 0.3,
                  "agreeableness": 0.6, "neuroticism": 0.4},
        "panas": {"positive_affect": 0.3, "negative_affect": 0.7},
    }
    s = analysis.format_report(results["pad"], "ласка", results)
    assert "EmoLex" in s and "Big Five" in s and "PANAS" in s and "Dostoevsky" in s
    assert "ласка" in s  # выбранное лицо в строке PAD
    assert "sadness" in s
