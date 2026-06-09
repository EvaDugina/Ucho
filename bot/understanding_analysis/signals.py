"""Локальные подсказки для анализа 02 без локальных ML-моделей.

Этот модуль не принимает решений о записи атомов. Он только собирает компактный
контекст для API-LLM: простые маркеры источников знания, убеждений, правил,
причинных объяснений, self-model и отношения к неизвестности.
"""
from __future__ import annotations

import re
from typing import Any

from . import taxonomy

_TOKEN_RE = re.compile(r"[а-яёa-z]+", re.IGNORECASE)

_MARKERS: dict[str, tuple[str, ...]] = {
    "knowledge": (
        "доказ", "факт", "наук", "исслед", "статист", "эксперимент",
        "образован", "учил", "книг", "авторитет", "традиц", "интуиц",
        "опыт", "провер",
    ),
    "beliefs": (
        "считаю", "верю", "убежд", "люди", "общество", "мир", "добро",
        "зло", "справедлив", "судьб", "свобод", "власть", "успех",
        "счаст", "смерт", "будущ",
    ),
    "principles": (
        "нельзя", "нужно", "надо", "должен", "принцип", "правило",
        "обещ", "слово", "границ", "достоин", "факты", "сомнев",
        "последств", "предав",
    ),
    "causality": (
        "потому", "из-за", "причин", "следств", "виноват", "ответствен",
        "система", "обстоятель", "характер", "травм", "выбор", "привыч",
        "случайн", "закономер", "хаос",
    ),
    "self_world_model": (
        "я", "могу", "способ", "огранич", "моя", "моё", "мной",
        "роль", "место", "сила", "слабость", "ответственность",
        "завис", "свобод", "долг", "границы",
    ),
    "uncertainty": (
        "не знаю", "неясн", "сомнев", "возможно", "вероят", "скорее",
        "доказ", "ошиб", "контрол", "тайн", "неизвест", "пересмотр",
        "импровиз", "вариант",
    ),
}


def _compact(value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    return {k: v for k, v in value.items() if v is not None}


def marker_hits(text: str) -> dict[str, list[str]]:
    """Вернуть категории, где текст содержит простые маркерные корни."""
    lowered = " ".join(_TOKEN_RE.findall((text or "").lower()))
    out: dict[str, list[str]] = {}
    if not lowered:
        return out
    for category, markers in _MARKERS.items():
        hits = [m for m in markers if m in lowered]
        if hits:
            out[category] = hits[:8]
    return out


def build_signals(
    text: str,
    *,
    mood_vec: dict | None = None,
    vad: dict | None = None,
    method_results: dict | None = None,
) -> dict[str, Any]:
    """Собрать compact JSON-like подсказки для LLM.

    Сигналы не содержат финальных `theme`/`name` решений и не должны напрямую
    попадать в граф.
    """
    methods = method_results if isinstance(method_results, dict) else {}
    return {
        "area": taxonomy.AREA_KEY,
        "text_len": len(text or ""),
        "pad": _compact(mood_vec) if isinstance(mood_vec, dict) else None,
        "vad": _compact(vad) if isinstance(vad, dict) else None,
        "emolex": _compact(methods.get("emolex")) if isinstance(methods.get("emolex"), dict) else None,
        "dostoevsky": _compact(methods.get("dostoevsky")) if isinstance(methods.get("dostoevsky"), dict) else None,
        "marker_categories": marker_hits(text),
    }
