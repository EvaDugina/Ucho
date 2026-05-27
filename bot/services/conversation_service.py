"""Application-сценарий обработки ответа в открытой probe-сессии."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from .. import about, analysis, graph, lexicon, mood_file, moods, session, session_log, vault
from ..config import ANALYSIS_ENABLED, DOMAINS
from ..llm import classify_mood, process_answer
from .answer_service import apply_processed
from .session_messages import question_field_with_face

log = logging.getLogger(__name__)

_DIRECTION_RU = {"auto": "на себя", "hetero": "на других/мир", "neutral": "нейтрально"}


@dataclass
class ReactionPayload:
    q_num: int
    mode: str
    domain: str
    text: str
    bot_mood: str | None
    answered_q_num: int | None
    answered_question: str
    session_id: str
    user_text: str
    session_context: str
    reply_to_user_message_id: int | None
    mood_message: str | None = None


def real_domain(d: str | None) -> str | None:
    return d if d in DOMAINS else None


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
            f"Q{e['n']} · {e['date']} {e['time']} · {e['domain']}\n"
            f"Q: {e['question']}\nA: {e['answer']}"
        )
    text = "\n\n".join(parts)
    if len(text) > max_chars:
        text = text[-max_chars:]
    return text


def context_for_domain(domain: str | None) -> str:
    concepts = graph.find_concepts(domain=domain, limit=40) if domain in DOMAINS else graph.find_concepts(limit=40)
    return graph.context_snapshot(concepts)


async def process_probe_answer(
    text: str,
    *,
    message_id: int | None = None,
    at: object | None = None,
    reply_to_message_id: int | None = None,
    is_owner: bool = False,
) -> ReactionPayload | None:
    """Обработать уже принятый user text и вернуть payload реакции для отправки."""
    s = session.get()
    if s is None:
        return None
    if s.current_q_num is None:
        log.warning("session has no current_q_num; assigning fresh")
        s.current_q_num = vault.next_q_num()
        session.persist()
    real_hint = real_domain(s.last_domain) or real_domain(s.domain)
    event = session_log.append_required(
        session_id=s.id,
        role="user",
        kind="answer",
        text=text,
        at=at,
        message_id=message_id,
        reply_to_message_id=reply_to_message_id,
        q_num=s.current_q_num,
        domain=real_hint,
    )
    s.pending_answer_event_id = event.get("event_id")
    s.pending_answer = None
    session.persist()
    s.record_user(text, at=at)
    session_context = s.render_transcript()
    context_concepts = context_for_domain(real_hint)

    mood_vec = None
    bot_mood = None
    vad = None
    mood_message = None
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
        question=s.last_question,
        answer=text,
        domain_hint=real_hint,
        context_concepts=context_concepts,
        bot_mood=bot_mood,
        session_context=session_context,
        mode=s.mode,
    )
    moods.record_mask_frequency_draft(
        result.get("mask_frequency_draft"),
        bot_mood=bot_mood,
        at=at,
    )

    try:
        apply_processed(result, s.current_q_num, s.asked_at, s.last_question, text, session_domain=real_hint)
    except Exception:
        log.exception("apply_processed failed")

    s.pending_answer = None
    s.pending_answer_event_id = None
    session.persist()

    if mood_vec and bot_mood:
        moods.log_turn(mood_vec, bot_mood, vad=vad)

    reaction = (result.get("reaction") or "").strip() or "Складно. Слишком складно."
    answered_question = s.last_question
    answered_q_num = s.current_q_num
    new_n = vault.next_q_num()
    next_domain = s.last_domain if s.last_domain in DOMAINS else "everyday"
    session.set_question(question_field_with_face(reaction, bot_mood), next_domain, q_num=new_n)
    session.persist()
    return ReactionPayload(
        q_num=new_n,
        mode=s.mode,
        domain=next_domain,
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
