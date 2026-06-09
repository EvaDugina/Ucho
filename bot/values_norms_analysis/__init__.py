"""API-only анализ полного канона `03_Ценностно-нормативная подсистема`.

Пакет не поднимает локальные ML-модели и не пишет в vault напрямую. Он даёт
валидированные draft-кандидаты, которые вызывающий сервис добавляет к обычным
`worldview_observations`.
"""
from __future__ import annotations

from .analyzer import analyze_values_norms, append_report, merge_into_processed
from .models import ValuesNormsAnalysisResult, ValuesNormsCandidate

__all__ = [
    "ValuesNormsAnalysisResult",
    "ValuesNormsCandidate",
    "analyze_values_norms",
    "append_report",
    "merge_into_processed",
]
