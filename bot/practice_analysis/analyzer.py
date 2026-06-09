"""Оркестратор API-анализа области 04_Практический уровень."""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from .. import vault
from ..atomic import atomic_write_text
from ..llm import analyze_practice_json
from .models import PracticeAnalysisResult
from .prompt import build_taxonomy_context, format_signals
from .signals import build_signals
from .validation import validate_candidates

log = logging.getLogger(__name__)


def _raw_candidates(data: object) -> list[dict[str, Any]]:
    if not isinstance(data, dict):
        return []
    for key in ("candidates", "practice_candidates", "worldview_observations", "observations"):
        value = data.get(key)
        if isinstance(value, list):
            return value
    return []


async def analyze_practice(
    answer: str,
    *,
    question: str = "",
    session_context: str = "",
    target: dict | None = None,
    mood_vec: dict | None = None,
    vad: dict | None = None,
    method_results: dict | None = None,
) -> PracticeAnalysisResult:
    """Получить валидированные draft-кандидаты 04.

    Функция не пишет в vault. Запись атомов делает вызывающий сервис через
    `answer_service.apply_processed`.
    """
    signals = build_signals(answer, mood_vec=mood_vec, vad=vad, method_results=method_results)
    taxonomy_context = build_taxonomy_context(target, signals)
    signal_context = format_signals(signals)
    try:
        data = await analyze_practice_json(
            answer=answer,
            question=question,
            session_context=session_context,
            taxonomy_context=taxonomy_context,
            signal_context=signal_context,
            target=target,
        )
    except Exception as exc:
        log.exception("practice analysis failed (non-fatal)")
        return PracticeAnalysisResult(signals=signals, warnings=[str(exc)])

    raw = _raw_candidates(data)
    candidates, dropped = validate_candidates(raw, answer)
    return PracticeAnalysisResult(
        candidates=candidates,
        signals=signals,
        raw_count=len(raw),
        dropped_count=dropped,
    )


def merge_into_processed(result: dict, practice: PracticeAnalysisResult | None) -> dict:
    """Добавить валидные 04-кандидаты к process-result без смены контракта."""
    if not isinstance(result, dict) or not practice or not practice.candidates:
        return result
    observations = list(result.get("worldview_observations") or result.get("observations") or [])
    observations.extend(candidate.to_observation() for candidate in practice.candidates)
    result["worldview_observations"] = observations
    result["observations"] = observations
    return result


def append_report(q_num: int | None, text_len: int, practice: PracticeAnalysisResult) -> None:
    """Дописать диагностический отчёт в `04_Практический уровень/analysis04/`."""
    try:
        now = datetime.now()
        d = vault.worldview_area_dir("practice") / "analysis04"
        d.mkdir(parents=True, exist_ok=True)
        p = d / f"{now:%Y-%m-%d}.md"
        if p.exists():
            body = p.read_text(encoding="utf-8").rstrip() + "\n\n"
        else:
            body = f"# Анализ 04_Практический уровень · {now:%Y-%m-%d}\n\n"
        q_label = f"Q{q_num}" if q_num is not None else "Q?"
        body += (
            f"## {now:%H:%M} · {q_label} · len={text_len}\n\n"
            f"- candidates: {len(practice.candidates)}\n"
            f"- raw: {practice.raw_count}\n"
            f"- dropped: {practice.dropped_count}\n"
        )
        marker_categories = practice.signals.get("marker_categories")
        if marker_categories:
            body += f"- marker_categories: {marker_categories}\n"
        if practice.warnings:
            body += "- warnings: " + "; ".join(practice.warnings[:3]) + "\n"
        for candidate in practice.candidates:
            body += (
                f"\n### {candidate.category}/{candidate.theme} · {candidate.name}\n\n"
                f"- type: {candidate.type}\n"
                f"- confidence: {candidate.confidence}\n"
                f"- reason: {candidate.evidence_reason or '—'}\n"
                f"- quote: {candidate.quote}\n"
                f"- summary: {candidate.summary}\n"
            )
        atomic_write_text(p, body)
    except Exception:
        log.exception("practice append_report failed (non-fatal)")
