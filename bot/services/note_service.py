"""Application-сценарий свободной заметки (/ucho и fallback-note)."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

from .. import moods, session, vault
from ..config import DOMAINS
from ..errors import LLMError
from ..llm import process_answer
from .answer_service import apply_processed
from .conversation_service import context_for_domain
from .session_messages import question_field_with_face

log = logging.getLogger(__name__)


@dataclass
class NoteReactionPayload:
    q_num: int
    mode: str
    domain: str
    text: str
    bot_mood: str | None = None
    answered_q_num: int | None = None
    answered_question: str = "(свободная заметка)"
    session_id: str | None = None
    user_text: str = ""
    session_context: str = ""
    reply_to_user_message_id: int | None = None


async def ingest_note(clean: str, *, at: datetime | None = None) -> NoteReactionPayload | None:
    """Сохранить note verbatim, разобрать в граф и вернуть reaction payload.

    Если note-запись не удалась — исключение пробрасывается наружу. Если LLM
    недоступна — возвращается None: заметка уже сохранена, но пользовательский
    статус не отправляется.
    """
    if session.get() is None:
        session.start(mode="probe", domain=None)
    s = session.get()
    if s is not None:
        s.record_user(clean, at=at)
        session_context = s.render_transcript()
    else:
        session_context = ""

    when = at if isinstance(at, datetime) else datetime.now()
    vault.append_note(when, clean)
    # Durability boundary: note is already in 00_raw/notes before any LLM work.
    # commit_all is best-effort when git is unavailable, but scoped when it is.
    vault.commit_all(f"ucho note saved {when.strftime('%Y-%m-%d %H:%M')}")

    q_num = vault.next_q_num()
    note_question = "(свободная заметка)"
    bot_mood = moods.random_bot_mood()
    try:
        result = await process_answer(
            question=note_question,
            answer=clean,
            domain_hint=None,
            context_concepts=context_for_domain(None),
            bot_mood=bot_mood,
            session_context=session_context,
            mode="probe",
        )
    except LLMError:
        log.warning("process_answer LLM error in note ingest")
        return None

    moods.record_mask_frequency_draft(
        result.get("mask_frequency_draft"),
        bot_mood=bot_mood,
        at=when,
    )

    try:
        apply_processed(result, q_num, when, note_question, clean)
    except Exception:
        log.exception("apply_processed failed in note ingest")
    vault.commit_all(f"ucho note {when.strftime('%Y-%m-%d %H:%M')}")

    reaction = (result.get("reaction") or "").strip() or "Складно. Слишком складно."
    new_n = vault.next_q_num()
    next_domain = "everyday"
    if s is not None:
        session.set_question(question_field_with_face(reaction, bot_mood), next_domain, q_num=new_n)
        session.persist()
        mode = s.mode
        session_id = s.id
    else:
        mode = "probe"
        session_id = None
    if next_domain not in DOMAINS:
        next_domain = "everyday"
    return NoteReactionPayload(
        q_num=new_n,
        mode=mode,
        domain=next_domain,
        text=reaction,
        bot_mood=bot_mood,
        answered_q_num=q_num,
        answered_question=note_question,
        session_id=session_id,
        user_text=clean,
        session_context=session_context,
    )
