"""Локальные подсказки для анализа 03 без локальных ML-моделей.

Этот модуль не принимает решений о записи атомов. Он только собирает компактный
контекст для API-LLM: маркеры ценностей, идеалов, норм, табу, иерархий и
моральной оценки.
"""
from __future__ import annotations

import re
from typing import Any

from . import taxonomy

_TOKEN_RE = re.compile(r"[а-яёa-z]+", re.IGNORECASE)

_MARKERS: dict[str, tuple[str, ...]] = {
    "values": (
        "важн", "цен", "дорог", "главн", "ради", "смысл", "любов",
        "семь", "свобод", "справедлив", "достоин", "безопас", "развит",
        "истин", "красот", "верност", "милосерд",
    ),
    "ideals": (
        "идеал", "идеальн", "образец", "совершен", "стремл", "хочу быть",
        "должно быть", "каким должен", "пример", "мастер", "лидер",
    ),
    "norms": (
        "нужно", "надо", "должен", "должна", "обязан", "правил",
        "правильн", "нельзя", "принято", "уваж", "забот", "границ",
        "ответствен", "обещ", "честн", "вежлив", "долг",
    ),
    "taboos": (
        "никогда", "недопуст", "запрет", "табу", "не прощу", "нельзя",
        "предател", "унижен", "трус", "лож", "насил", "продаж",
        "подл", "эксплуатац", "жесток", "бесчест",
    ),
    "hierarchy": (
        "важнее", "главнее", "выше", "дороже", "ценнее", "между",
        "выбираю", "пожертв", "цена", "ради", "вместо", "vs",
    ),
    "judgement": (
        "виноват", "вина", "заслуж", "прощ", "наказ", "презир",
        "восхищ", "требу", "снисх", "стыд", "горд", "благодар",
        "осужд", "уважаю",
    ),
}


def _compact(value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    return {k: v for k, v in value.items() if v is not None}


def marker_hits(text: str) -> dict[str, list[str]]:
    """Вернуть категории, где текст содержит простые маркерные корни."""
    lowered = " ".join(_TOKEN_RE.findall((text or "").lower()))
    raw = (text or "").lower()
    out: dict[str, list[str]] = {}
    if not lowered and not raw:
        return out
    for category, markers in _MARKERS.items():
        hits = [m for m in markers if m in lowered or (" " in m and m in raw)]
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
