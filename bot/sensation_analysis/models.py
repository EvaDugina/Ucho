"""Типы результата анализа 01_Мироощущение."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class SensationCandidate:
    """Валидированный кандидат draft-атома области 01."""

    category: str
    theme: str
    name: str
    summary: str
    quote: str
    confidence: float
    evidence_reason: str = ""
    type: str = "feeling"

    def to_observation(self) -> dict[str, Any]:
        return {
            "area": "sensation",
            "category": self.category,
            "theme": self.theme,
            "type": self.type,
            "name": self.name,
            "summary": self.summary,
            "quote": self.quote,
            "confidence": self.confidence,
        }


@dataclass
class SensationAnalysisResult:
    """Результат API-анализа и его локального post-processing."""

    candidates: list[SensationCandidate] = field(default_factory=list)
    signals: dict[str, Any] = field(default_factory=dict)
    raw_count: int = 0
    dropped_count: int = 0
    warnings: list[str] = field(default_factory=list)

    @property
    def has_candidates(self) -> bool:
        return bool(self.candidates)
