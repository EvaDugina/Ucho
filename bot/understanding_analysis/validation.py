"""Валидация кандидатов анализа 02 до записи в граф."""
from __future__ import annotations

import re
from typing import Any

from .models import UnderstandingCandidate
from .taxonomy import is_valid_theme

MIN_CONFIDENCE = 0.62
MAX_CANDIDATES = 5
ALLOWED_TYPES = {"belief", "principle", "claim"}


def _clean_text(value: object, limit: int = 600) -> str:
    text = str(value or "").strip()
    text = re.sub(r"\s+", " ", text)
    return text[:limit].strip()


def _confidence(value: object) -> float | None:
    try:
        return round(max(0.0, min(1.0, float(value))), 2)
    except (TypeError, ValueError):
        return None


def _type_for(category: str, value: object) -> str:
    candidate_type = _clean_text(value, 40)
    if candidate_type in ALLOWED_TYPES:
        return candidate_type
    if category == "principles":
        return "principle"
    return "belief"


def contains_verbatim_quote(answer: str, quote: str) -> bool:
    """Проверить цитату как дословную подстроку с нормализацией пробелов."""
    a = " ".join((answer or "").split()).lower()
    q = " ".join((quote or "").split()).lower()
    return bool(q and q in a)


def validate_candidates(
    raw_candidates: object,
    answer: str,
    *,
    min_confidence: float = MIN_CONFIDENCE,
    max_candidates: int = MAX_CANDIDATES,
) -> tuple[list[UnderstandingCandidate], int]:
    """Вернуть валидные кандидаты и число отброшенных элементов."""
    if not isinstance(raw_candidates, list):
        return [], 0
    out: list[UnderstandingCandidate] = []
    dropped = 0
    seen: set[tuple[str, str, str, str]] = set()
    for index, item in enumerate(raw_candidates):
        if not isinstance(item, dict):
            dropped += 1
            continue
        category = _clean_text(item.get("category"), 80)
        theme = _clean_text(item.get("theme"), 120)
        name = _clean_text(item.get("name"), 160)
        summary = _clean_text(item.get("summary"), 600)
        quote = _clean_text(item.get("quote"), 600)
        confidence = _confidence(item.get("confidence"))
        if (
            not is_valid_theme(category, theme)
            or not name
            or not summary
            or confidence is None
            or confidence < min_confidence
            or not contains_verbatim_quote(answer, quote)
        ):
            dropped += 1
            continue
        key = (category, theme, name.lower(), quote.lower())
        if key in seen:
            dropped += 1
            continue
        seen.add(key)
        out.append(
            UnderstandingCandidate(
                category=category,
                theme=theme,
                type=_type_for(category, item.get("type")),
                name=name,
                summary=summary,
                quote=quote,
                confidence=confidence,
                evidence_reason=_clean_text(item.get("evidence_reason"), 400),
            )
        )
        if len(out) >= max_candidates:
            # Остальное не ошибка модели, но для V1 держим bounded payload.
            dropped += len(raw_candidates) - index - 1
            break
    return out, dropped
