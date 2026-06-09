"""Локальные подсказки для анализа 04 без локальных ML-моделей.

Этот модуль не принимает решений о записи атомов. Он только собирает компактный
контекст для API-LLM: маркеры готовности, воли, стиля жизни, поступков,
стратегий совладания и последствий выбора.
"""
from __future__ import annotations

import re
from typing import Any

from . import taxonomy

_TOKEN_RE = re.compile(r"[а-яёa-z]+", re.IGNORECASE)

_MARKERS: dict[str, tuple[str, ...]] = {
    "readiness": (
        "готов", "собира", "решил", "решить", "начн", "начать", "пора",
        "могу", "риск", "призн", "попрос", "откаж", "уйти", "защит",
        "ответствен", "вмеш", "заверш", "восстанов",
    ),
    "will": (
        "держ", "сорвал", "застав", "режим", "привыч", "дисциплин",
        "терп", "лен", "страх", "самоконт", "упор", "выдерж",
        "отказ", "давлен", "вол",
    ),
    "lifestyle": (
        "обычно", "каждый день", "регуляр", "работ", "сплю", "сон",
        "пита", "еда", "трачу", "деньги", "медиа", "ритуал", "режим",
        "быт", "отдых", "отнош", "социальн", "обуч", "простран",
        "здоров", "хаос", "поряд",
    ),
    "actions": (
        "помог", "отказ", "попрос", "разорвал", "разрыв", "помир",
        "примир", "признал", "ошиб", "обещ", "наруш", "защит",
        "границ", "уступ", "борол", "бежал", "сделк", "жертв", "мест",
    ),
    "strategies": (
        "избег", "контрол", "переговор", "дав", "терп", "ирон",
        "рационализ", "план", "импровиз", "поддерж", "изоля",
        "конфронт", "подстрой", "работу", "фантаз", "риск",
    ),
    "consequences": (
        "в итоге", "из-за", "после этого", "цена", "потер", "приобр",
        "стало легче", "облегч", "разруш", "укреп", "вина", "рост",
        "деградац", "побед", "поражен", "компромисс", "самообман",
        "паттерн",
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
