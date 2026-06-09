"""Типы результата анализа 03_Ценностно-нормативная подсистема."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ValuesNormsCandidate:
    """Валидированный кандидат draft-атома области 03."""

    category: str
    theme: str
    name: str
    summary: str
    quote: str
    confidence: float
    evidence_reason: str = ""
    type: str = "value"

    def to_observation(self) -> dict[str, Any]:
        return {
            "area": "values_norms",
            "category": self.category,
            "theme": self.theme,
            "type": self.type,
            "name": self.name,
            "summary": self.summary,
            "quote": self.quote,
            "confidence": self.confidence,
        }


@dataclass
class ValuesNormsAnalysisResult:
    """Результат API-анализа и локального post-processing."""

    candidates: list[ValuesNormsCandidate] = field(default_factory=list)
    signals: dict[str, Any] = field(default_factory=dict)
    raw_count: int = 0
    dropped_count: int = 0
    warnings: list[str] = field(default_factory=list)

    @property
    def has_candidates(self) -> bool:
        return bool(self.candidates)
