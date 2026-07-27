"""Обработка ответа: raw → mood → personality delta → реакция."""
from __future__ import annotations

import logging
from dataclasses import dataclass

from .. import about, mood_file, moods, session, session_log, vault
from ..config import DOMAINS
from ..llm import classify_mood, process_answer
from .answer_service import apply_processed
from .session_messages import question_field_with_face

log = logging.getLogger(__name__)


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


def real_domain(value: str | None) -> str | None:
    return value if value in DOMAINS else None


async def process_probe_answer(
    text: str,
    *,
    message_id: int | None = None,
    at: object | None = None,
    reply_to_message_id: int | None = None,
    is_owner: bool = False,
    question: str | None = None,
    domain_hint: str | None = None,
    q_num: int | None = None,
    session_context_snapshot: str | None = None,
    mode: str | None = None,
    event_kind: str = "answer",
    metadata: dict | None = None,
) -> ReactionPayload | None:
    _ = is_owner
    current = session.get()
    if current is None:
        return None
    active_q_num = q_num if q_num is not None else current.current_q_num
    if active_q_num is None:
        active_q_num = vault.next_q_num()
    active_question = question if question is not None else current.last_question
    active_domain = real_domain(domain_hint) or real_domain(current.last_domain) or "everyday"
    active_mode = mode or current.mode

    event = session_log.append_required(
        session_id=current.id,
        role="user",
        kind=event_kind,
        text=text,
        at=at,
        message_id=message_id,
        reply_to_message_id=reply_to_message_id,
        q_num=active_q_num,
        domain=active_domain,
        metadata=metadata or current.question_metadata,
    )
    current.pending_answer_event_id = event["event_id"]
    current.pending_answer = text
    session.persist()
    vault.commit_all(f"raw {event_kind}")

    session_context = session_context_snapshot or current.render_transcript()
    mood_vec: dict | None = None
    bot_mood: str | None = None
    try:
        per_message = await classify_mood(
            text,
            about.render_for_prompt(),
            session_context=session_context,
        )
        current.record_mood(per_message)
        mood_vec = moods.session_mood(current.mood_trajectory, mood_file.baseline())
        bot_mood = moods.pick_bot_mood(mood_vec)
        mood_file.set_current(mood_vec, bot_mood)
    except Exception:
        log.exception("mood detection failed (non-fatal)")
    if bot_mood is None:
        bot_mood = moods.random_bot_mood()

    result = await process_answer(
        question=active_question,
        answer=text,
        domain_hint=active_domain,
        bot_mood=bot_mood,
        session_context=session_context,
        mode=active_mode,
        metadata=metadata or current.question_metadata,
    )
    moods.record_mask_frequency_draft(
        result.get("mask_frequency_draft"),
        bot_mood=bot_mood,
        at=at,
    )
    apply_processed(
        result,
        raw_event_id=event["event_id"],
        original_answer=text,
        at=at,
    )

    current.pending_answer = None
    current.pending_answer_event_id = None
    session.persist()
    if mood_vec:
        moods.log_turn(
            mood_vec,
            bot_mood,
            raw_event_id=event["event_id"],
            session_id=current.id,
            q_num=active_q_num,
        )

    reaction = str(result.get("reaction") or "").strip() or "Складно. Слишком складно."
    new_q_num = vault.next_q_num()
    session.set_question(
        question_field_with_face(reaction, bot_mood),
        active_domain,
        q_num=new_q_num,
    )
    return ReactionPayload(
        q_num=new_q_num,
        mode=active_mode,
        domain=active_domain,
        text=reaction,
        bot_mood=bot_mood,
        answered_q_num=active_q_num,
        answered_question=active_question,
        session_id=current.id,
        user_text=text,
        session_context=session_context,
        reply_to_user_message_id=message_id,
    )
