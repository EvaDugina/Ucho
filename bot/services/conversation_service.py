"""Application-сценарий обработки ответа в открытой probe-сессии."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from .. import about, analysis, lexicon, mood_file, moods, session, session_log, vault, worldview
from ..config import ANALYSIS_ENABLED
from ..llm import classify_mood, process_answer
from ..sensation_analysis import (
    analyze_sensation,
    append_report as append_sensation_report,
    merge_into_processed,
)
from ..worldview_taxonomy import coerce_target, get_area, legacy_domain_target
from .answer_service import apply_processed
from .session_messages import question_field_with_face

log = logging.getLogger(__name__)

_DIRECTION_RU = {"auto": "на себя", "hetero": "на других/мир", "neutral": "нейтрально"}


@dataclass
class ReactionPayload:
    q_num: int
    mode: str
    area: str
    category: str
    theme: str
    theme_key: str
    text: str
    bot_mood: str | None
    answered_q_num: int | None
    answered_question: str
    session_id: str
    user_text: str
    session_context: str
    reply_to_user_message_id: int | None
    domain: str = ""
    mood_message: str | None = None


def real_domain(d: str | None) -> str | None:
    return d if legacy_domain_target(d) is not None else None


def _target_from_values(
    *,
    area: str | None = None,
    category: str | None = None,
    theme: str | None = None,
    domain: str | None = None,
    fallback: dict | None = None,
) -> dict:
    if area and get_area(area):
        return coerce_target(area, category, theme)
    legacy = legacy_domain_target(domain or area)
    if legacy:
        return legacy
    if fallback:
        return coerce_target(fallback.get("area"), fallback.get("category"), fallback.get("theme"))
    return coerce_target(None, None, None)


def format_mood(mv: dict, bot_mood: str | None, vad: dict | None = None) -> str:
    lines = [
        "🎭 Настроение",
        f"эмоция: {mv.get('quality', '—')}",
        f"валентность: {mv.get('valence')} ({mv.get('sign')})",
        f"энергия: {mv.get('energy')} (arousal {mv.get('arousal')})",
        f"доминирование: {mv.get('dominance_label')} ({mv.get('dominance')})",
        f"направленность: {_DIRECTION_RU.get(mv.get('direction'), mv.get('direction'))}",
        f"устойчивость: {mv.get('stability')}",
        f"лицо: {bot_mood or '—'}",
    ]
    if isinstance(vad, dict):
        lines.append(
            f"лексикон VAD: v={vad.get('valence')} a={vad.get('arousal')} "
            f"d={vad.get('dominance')} (слов: {vad.get('n')})"
        )
    return "\n".join(lines)


def recent_raw_text(days: int = 7, max_chars: int = 8000) -> str:
    cutoff = datetime.now() - timedelta(days=days)
    rows = []
    for e in vault.iter_history():
        try:
            dt = datetime.fromisoformat(f"{e['date']}T{e['time']}:00")
        except Exception:
            continue
        if dt >= cutoff:
            rows.append(e)
    parts: list[str] = []
    for e in rows[-50:]:
        parts.append(
            f"Q{e['n']} · {e['date']} {e['time']} · {e.get('theme_key') or e.get('domain')}\n"
            f"Q: {e['question']}\nA: {e['answer']}"
        )
    text = "\n\n".join(parts)
    if len(text) > max_chars:
        text = text[-max_chars:]
    return text


def context_for_target(area: str | None = None, category: str | None = None, theme: str | None = None) -> str:
    target = _target_from_values(area=area, category=category, theme=theme)
    atoms = worldview.find_atoms(area=target["area"], category=target["category"], limit=40)
    if not atoms:
        atoms = worldview.find_atoms(area=target["area"], limit=40)
    if not atoms:
        atoms = worldview.find_atoms(limit=40)
    return worldview.context_snapshot(atoms)


def context_for_domain(domain: str | None) -> str:
    """Legacy wrapper for old callers/tests."""
    target = _target_from_values(domain=domain)
    return context_for_target(target["area"], target["category"], target["theme"])


def _coerce_datetime(value: object | None, fallback: datetime) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return fallback
    return fallback


async def process_probe_answer(
    text: str,
    *,
    message_id: int | None = None,
    at: object | None = None,
    reply_to_message_id: int | None = None,
    is_owner: bool = False,
    question: str | None = None,
    domain_hint: str | None = None,
    area: str | None = None,
    category: str | None = None,
    theme: str | None = None,
    q_num: int | None = None,
    asked_at: object | None = None,
    session_context_snapshot: str | None = None,
    mode: str | None = None,
) -> ReactionPayload | None:
    """Обработать уже принятый user text и вернуть payload реакции для отправки."""
    s = session.get()
    if s is None:
        return None
    if s.current_q_num is None and q_num is None:
        log.warning("session has no current_q_num; assigning fresh")
        s.current_q_num = vault.next_q_num()
        session.persist()
    active_q_num = q_num if q_num is not None else s.current_q_num
    active_question = question if question is not None else s.last_question
    active_target = _target_from_values(
        area=area or s.last_area or s.area,
        category=category or s.last_category or s.category,
        theme=theme or s.last_theme or s.theme,
        domain=domain_hint or s.last_domain or s.domain,
    )
    active_asked_at = _coerce_datetime(asked_at, s.asked_at)
    active_mode = mode or s.mode
    if active_q_num is None:
        active_q_num = vault.next_q_num()
    s.current_q_num = active_q_num
    s.last_question = active_question
    s.last_area = active_target["area"]
    s.last_category = active_target["category"]
    s.last_theme = active_target["theme"]
    s.last_theme_key = active_target["theme_key"]
    s.last_domain = str(domain_hint or s.last_domain or "")
    s.asked_at = active_asked_at
    event = session_log.append_required(
        session_id=s.id,
        role="user",
        kind="answer",
        text=text,
        at=at,
        message_id=message_id,
        reply_to_message_id=reply_to_message_id,
        q_num=active_q_num,
        area=active_target["area"],
        category=active_target["category"],
        theme=active_target["theme"],
        theme_key=active_target["theme_key"],
        domain=domain_hint,
    )
    s.pending_answer_event_id = event.get("event_id")
    s.pending_answer = None
    session.persist()
    s.record_user(text, at=at)
    session_context = session_context_snapshot or s.render_transcript()
    context_atoms = context_for_target(active_target["area"], active_target["category"], active_target["theme"])

    mood_vec = None
    bot_mood = None
    vad = None
    mood_message = None
    analysis_results = None
    if is_owner:
        try:
            vad = await lexicon.score(text)
            per_msg = await classify_mood(
                text, about.render_for_prompt(), vad=vad, session_context=session_context
            )
            s.record_mood(per_msg)
            mood_vec = moods.session_mood(s.mood_trajectory, mood_file.baseline())
            bot_mood = moods.pick_bot_mood(mood_vec)
            mood_file.set_current(mood_vec, bot_mood)
            try:
                if ANALYSIS_ENABLED:
                    results = await analysis.run_all(
                        text, None, mood_vec=mood_vec, vad=vad, session_context=session_context,
                    )
                    analysis_results = results
                    report = analysis.format_report(mood_vec, bot_mood, results)
                    analysis.append_report(s.current_q_num, len(text), report)
                    analysis.append_point(len(text), results)
                    analysis.rebuild_chart()
                else:
                    mood_message = format_mood(mood_vec, bot_mood, vad)
            except Exception:
                log.exception("analysis report failed (non-fatal)")
        except Exception:
            log.exception("mood detection failed (non-fatal)")
    if bot_mood is None:
        bot_mood = moods.random_bot_mood()

    result = await process_answer(
        question=active_question,
        answer=text,
        area=active_target["area"],
        category=active_target["category"],
        theme=active_target["theme"],
        context_atoms=context_atoms,
        bot_mood=bot_mood,
        session_context=session_context,
        mode=active_mode,
    )
    moods.record_mask_frequency_draft(
        result.get("mask_frequency_draft"),
        bot_mood=bot_mood,
        at=at,
    )
    if is_owner and ANALYSIS_ENABLED:
        try:
            sensation = await analyze_sensation(
                text,
                question=active_question,
                session_context=session_context,
                target=active_target,
                mood_vec=mood_vec,
                vad=vad,
                method_results=analysis_results,
            )
            append_sensation_report(active_q_num, len(text), sensation)
            result = merge_into_processed(result, sensation)
        except Exception:
            log.exception("sensation analysis failed (non-fatal)")

    try:
        apply_processed(result, active_q_num, active_asked_at, active_question, text, target=active_target, session_domain=domain_hint)
    except Exception:
        log.exception("apply_processed failed")

    s.pending_answer = None
    s.pending_answer_event_id = None
    session.persist()

    if mood_vec and bot_mood:
        moods.log_turn(mood_vec, bot_mood, vad=vad)

    reaction = (result.get("reaction") or "").strip() or "Складно. Слишком складно."
    answered_question = active_question
    answered_q_num = active_q_num
    new_n = vault.next_q_num()
    session.set_question(question_field_with_face(reaction, bot_mood), target=active_target, q_num=new_n, domain=domain_hint)
    session.persist()
    return ReactionPayload(
        q_num=new_n,
        mode=active_mode,
        area=active_target["area"],
        category=active_target["category"],
        theme=active_target["theme"],
        theme_key=active_target["theme_key"],
        domain=domain_hint or "",
        text=reaction,
        bot_mood=bot_mood,
        answered_q_num=answered_q_num,
        answered_question=answered_question,
        session_id=s.id,
        user_text=text,
        session_context=session_context,
        reply_to_user_message_id=message_id,
        mood_message=mood_message,
    )
