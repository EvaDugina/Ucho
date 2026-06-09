"""API-only анализ полного канона `04_Практический уровень`.

Пакет не поднимает локальные ML-модели и не пишет в vault напрямую. Он даёт
валидированные draft-кандидаты, которые вызывающий сервис добавляет к обычным
`worldview_observations`.
"""
from __future__ import annotations

from .analyzer import analyze_practice, append_report, merge_into_processed
from .models import PracticeAnalysisResult, PracticeCandidate

__all__ = [
    "PracticeAnalysisResult",
    "PracticeCandidate",
    "analyze_practice",
    "append_report",
    "merge_into_processed",
]
