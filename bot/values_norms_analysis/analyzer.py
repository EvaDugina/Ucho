"""Оркестратор API-анализа области 03_Ценностно-нормативная подсистема."""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from .. import vault
from ..atomic import atomic_write_text
from ..llm import analyze_values_norms_json
from .models import ValuesNormsAnalysisResult
from .prompt import build_taxonomy_context, format_signals
from .signals import build_signals
from .validation import validate_candidates

log = logging.getLogger(__name__)


def _raw_candidates(data: object) -> list[dict[str, Any]]:
    if not isinstance(data, dict):
        return []
    for key in ("candidates", "values_norms_candidates", "worldview_observations", "observations"):
        value = data.get(key)
        if isinstance(value, list):
            return value
    return []


async def analyze_values_norms(
    answer: str,
    *,
    question: str = "",
    session_context: str = "",
    target: dict | None = None,
    mood_vec: dict | None = None,
    vad: dict | None = None,
    method_results: dict | None = None,
) -> ValuesNormsAnalysisResult:
    """Получить валидированные draft-кандидаты 03.

    Функция не пишет в vault. Запись атомов делает вызывающий сервис через
    `answer_service.apply_processed`.
    """
    signals = build_signals(answer, mood_vec=mood_vec, vad=vad, method_results=method_results)
    taxonomy_context = build_taxonomy_context(target, signals)
    signal_context = format_signals(signals)
    try:
        data = await analyze_values_norms_json(
            answer=answer,
            question=question,
            session_context=session_context,
            taxonomy_context=taxonomy_context,
            signal_context=signal_context,
            target=target,
        )
    except Exception as exc:
        log.exception("values_norms analysis failed (non-fatal)")
        return ValuesNormsAnalysisResult(signals=signals, warnings=[str(exc)])

    raw = _raw_candidates(data)
    candidates, dropped = validate_candidates(raw, answer)
    return ValuesNormsAnalysisResult(
        candidates=candidates,
        signals=signals,
        raw_count=len(raw),
        dropped_count=dropped,
    )


def merge_into_processed(result: dict, values_norms: ValuesNormsAnalysisResult | None) -> dict:
    """Добавить валидные 03-кандидаты к process-result без смены контракта."""
    if not isinstance(result, dict) or not values_norms or not values_norms.candidates:
        return result
    observations = list(result.get("worldview_observations") or result.get("observations") or [])
    observations.extend(candidate.to_observation() for candidate in values_norms.candidates)
    result["worldview_observations"] = observations
    result["observations"] = observations
    return result


def append_report(q_num: int | None, text_len: int, values_norms: ValuesNormsAnalysisResult) -> None:
    """Дописать диагностический отчёт в `03_Ценностно-нормативная подсистема/analysis03/`."""
    try:
        now = datetime.now()
        d = vault.worldview_area_dir("values_norms") / "analysis03"
        d.mkdir(parents=True, exist_ok=True)
        p = d / f"{now:%Y-%m-%d}.md"
        if p.exists():
            body = p.read_text(encoding="utf-8").rstrip() + "\n\n"
        else:
            body = f"# Анализ 03_Ценностно-нормативная подсистема · {now:%Y-%m-%d}\n\n"
        q_label = f"Q{q_num}" if q_num is not None else "Q?"
        body += (
            f"## {now:%H:%M} · {q_label} · len={text_len}\n\n"
            f"- candidates: {len(values_norms.candidates)}\n"
            f"- raw: {values_norms.raw_count}\n"
            f"- dropped: {values_norms.dropped_count}\n"
        )
        marker_categories = values_norms.signals.get("marker_categories")
        if marker_categories:
            body += f"- marker_categories: {marker_categories}\n"
        if values_norms.warnings:
            body += "- warnings: " + "; ".join(values_norms.warnings[:3]) + "\n"
        for candidate in values_norms.candidates:
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
        log.exception("values_norms append_report failed (non-fatal)")
