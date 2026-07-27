"""Обработка ответа: raw → mood → personality delta → реакция."""
from __future__ import annotations

import logging
from dataclasses import dataclass

from .. import about, mood_file, moods, session, session_log, vault
from ..config import DOMAINS
from ..llm import classify_mood, process_answer
from .answer_service import apply_processed

log = logging.getLogger(__name__)


@dataclass
class ReactionPayload:
    q_num: int
    domain: str
    text: str
    reply_to_user_message_id: int | None


def real_domain(value: str | None) -> str | None:
    return value if value in DOMAINS else None


async def process_probe_answer(
    text: str,
    *,
    message_id: int | None = None,
    at: object | None = None,
    reply_to_message_id: int | None = None,
    question: str | None = None,
    domain_hint: str | None = None,
    q_num: int | None = None,
    session_context_snapshot: str | None = None,
    event_kind: str = "answer",
    metadata: dict | None = None,
) -> ReactionPayload | None:
    current = session.get()
    if current is None:
        return None
    active_q_num = q_num if q_num is not None else current.current_q_num
    if active_q_num is None:
        active_q_num = vault.next_q_num()
    active_question = question if question is not None else current.last_question
    active_domain = real_domain(domain_hint) or real_domain(current.last_domain) or "everyday"
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
    try:
        per_message = await classify_mood(
            text,
            about.render_for_prompt(),
            session_context=session_context,
        )
        current.record_mood(per_message)
        mood_vec = moods.session_mood(current.mood_trajectory, mood_file.baseline())
        mood_file.set_current(mood_vec)
        moods.log_turn(
            mood_vec,
            raw_event_id=str(event["event_id"]),
            session_id=current.id,
            q_num=active_q_num,
            at=event.get("ts"),
        )
        vault.commit_all("mood")
    except Exception:
        log.exception("mood detection failed (non-fatal)")

    result = await process_answer(
        question=active_question,
        answer=text,
        domain_hint=active_domain,
        session_context=session_context,
        metadata=metadata or current.question_metadata,
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

    reaction = str(result.get("reaction") or "").strip() or "Сообщение принято."
    new_q_num = vault.next_q_num()
    session.set_question(reaction, active_domain, q_num=new_q_num)
    return ReactionPayload(
        q_num=new_q_num,
        domain=active_domain,
        text=reaction,
        reply_to_user_message_id=message_id,
    )
