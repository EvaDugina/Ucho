"""Юнит-тесты форматирования отчёта сравнения методов (bot/analysis.py)."""
from __future__ import annotations

from bot import analysis


def test_format_report_handles_all_none():
    results = {k: None for k in ("pad", "emolex", "dostoevsky", "panas")}
    s = analysis.format_report(None, None, results)
    assert "Анализ ответа" in s
    # каждый метод деградирует до плейсхолдера, а не падает
    assert "нет" in s.lower()
    assert "PANAS" in s
    assert "Big Five" not in s and "OCEAN" not in s and "NRC-VAD" not in s


def test_format_report_full():
    results = {
        "pad": {"quality": "грусть_тоска", "valence": -0.5, "arousal": -0.5,
                "dominance": -0.5, "dominance_label": "low", "stability": "rigid"},
        "emolex": {"top": ["sadness", "fear"], "sadness": 0.5, "fear": 0.3,
                   "positive": 0.0, "negative": 0.5, "n": 3},
        "dostoevsky": {"label": "negative", "score": 0.8},
        "panas": {"positive_affect": 0.2, "negative_affect": 0.7, "source": "code"},
    }
    s = analysis.format_report(results["pad"], "ласка", results)
    assert "EmoLex" in s and "Dostoevsky" in s and "PANAS" in s
    assert "Big Five" not in s and "OCEAN" not in s and "NRC-VAD" not in s
    assert "выбранное лицо Иуды: ласка" in s
    assert "грусть" in s  # эмоция EmoLex переведена на русский
    assert "валентность" not in s and "доминирование" not in s and "устойчивость" not in s
    assert "негатив" in s and "негативный аффект: 0.7" in s


def test_append_report_writes_knowledge_note(as_user):
    report = "🧪 Анализ ответа — методы (число → пояснение)\n\n▸ PAD\nнет данных"
    analysis.append_report(q_num=42, text_len=123, report=report)

    files = sorted((analysis.vault.mood_dir() / "analysis").glob("*.md"))
    assert len(files) == 1
    text = files[0].read_text(encoding="utf-8")
    assert "# Анализ методов" in text
    assert "Q42" in text and "len=123" in text
    assert "Анализ ответа — методы" in text


def test_aggregate_daily_groups_and_averages():
    points = [
        {"ts": "2026-05-22T10:00:00", "pad": {"valence": -0.4, "arousal": 0.0, "dominance": -0.2}},
        {"ts": "2026-05-22T18:00:00", "pad": {"valence": -0.6, "arousal": 0.2, "dominance": -0.4}},
        {"ts": "2026-05-23T09:00:00", "pad": {"valence": 0.5, "arousal": 0.3, "dominance": 0.1}},
        {"ts": "2026-05-23T12:00:00", "pad": None},  # без PAD — игнор
        {"ts": "bad", "foo": 1},                      # мусор — игнор
    ]
    labels, series = analysis.aggregate_daily(points)
    assert labels == ["2026-05-22", "2026-05-23"]
    assert series["valence"] == [-0.5, 0.5]  # среднее по дню
    assert len(series["arousal"]) == 2 and len(series["dominance"]) == 2


def test_aggregate_daily_empty():
    labels, series = analysis.aggregate_daily([])
    assert labels == [] and series["valence"] == []
