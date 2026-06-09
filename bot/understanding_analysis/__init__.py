"""API-only анализ полного канона `02_Миропонимание`.

Пакет не поднимает локальные ML-модели и не пишет в vault напрямую. Он даёт
валидированные draft-кандидаты, которые вызывающий сервис добавляет к обычным
`worldview_observations`.
"""
from __future__ import annotations

from .analyzer import analyze_understanding, append_report, merge_into_processed
from .models import UnderstandingAnalysisResult, UnderstandingCandidate

__all__ = [
    "UnderstandingAnalysisResult",
    "UnderstandingCandidate",
    "analyze_understanding",
    "append_report",
    "merge_into_processed",
]
