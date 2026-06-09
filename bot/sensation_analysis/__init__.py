"""Анализ области 01_Мироощущение через API-LLM и лёгкие локальные сигналы.

Пакет не поднимает локальные ML-модели: все смысловые решения принимает только
live provider из `bot.llm`, а локальный код даёт подсказки и валидирует evidence.
"""
from .analyzer import analyze_sensation, append_report, merge_into_processed
from .models import SensationAnalysisResult, SensationCandidate

__all__ = [
    "SensationAnalysisResult",
    "SensationCandidate",
    "analyze_sensation",
    "append_report",
    "merge_into_processed",
]
