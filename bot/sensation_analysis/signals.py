"""Локальные подсказки для анализа 01 без локальных ML-моделей.

Этот модуль не принимает решений о записи атомов. Он только собирает компактный
контекст для API-LLM: уже посчитанный PAD/VAD, результаты существующих методов и
простые маркеры категорий.
"""
from __future__ import annotations

import re
from typing import Any

from . import taxonomy

_TOKEN_RE = re.compile(r"[а-яёa-z]+", re.IGNORECASE)

_MARKERS: dict[str, tuple[str, ...]] = {
    "emotions": (
        "радость", "рад", "страх", "боюсь", "тревог", "стыд", "вина",
        "злюсь", "гнев", "обид", "печал", "тоск", "люблю", "нежн",
    ),
    "mood_background": (
        "фон", "обычно", "постоянно", "всегда", "часто", "апат",
        "подавлен", "скука", "спокойн", "напряж", "насторож",
    ),
    "world_tone": (
        "мир", "жизнь", "люди", "враждеб", "опасн", "равнодуш",
        "добрый", "справедлив", "несправедлив", "абсурд", "закрыт",
    ),
    "beauty_ugliness": (
        "красив", "урод", "гармон", "пошл", "чист", "гряз",
        "велич", "уют", "фальш", "подлин", "свят", "оскверн",
    ),
    "body_and_energy": (
        "тело", "устал", "сил", "бол", "зажат", "сон", "перегруз",
        "бодр", "напряж", "расслаб", "истощ", "энерг", "дрож",
    ),
    "existential_feeling": (
        "одинок", "смысл", "бессмыс", "дом", "бездом", "чуж",
        "смерт", "конечн", "будущ", "призван", "обреч", "жизнь",
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
    markers = marker_hits(text)
    return {
        "area": taxonomy.AREA_KEY,
        "text_len": len(text or ""),
        "pad": _compact(mood_vec) if isinstance(mood_vec, dict) else None,
        "vad": _compact(vad) if isinstance(vad, dict) else None,
        "emolex": _compact(methods.get("emolex")) if isinstance(methods.get("emolex"), dict) else None,
        "dostoevsky": _compact(methods.get("dostoevsky")) if isinstance(methods.get("dostoevsky"), dict) else None,
        "panas": _compact(methods.get("panas")) if isinstance(methods.get("panas"), dict) else None,
        "marker_categories": markers,
    }
