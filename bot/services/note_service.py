"""Application-сценарий свободной заметки (/ucho и fallback-note)."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

from .. import moods, session, vault
from ..errors import LLMError
from ..llm import process_answer
from .answer_service import apply_processed
from .conversation_service import context_for_target
from .session_messages import question_field_with_face
from ..worldview_taxonomy import coerce_target

log = logging.getLogger(__name__)


@dataclass
class NoteReactionPayload:
    q_num: int
    mode: str
    area: str
    category: str
    theme: str
    theme_key: str
    text: str
    bot_mood: str | None = None
    answered_q_num: int | None = None
    answered_question: str = "(свободная заметка)"
    session_id: str | None = None
    user_text: str = ""
    session_context: str = ""
    reply_to_user_message_id: int | None = None
    domain: str = ""


async def ingest_note(clean: str, *, at: datetime | None = None) -> NoteReactionPayload | None:
    """Сохранить note verbatim, разобрать в граф и вернуть reaction payload.

    Если note-запись не удалась — исключение пробрасывается наружу. Если LLM
    недоступна — возвращается None: заметка уже сохранена, но пользовательский
    статус не отправляется.
    """
    if session.get() is None:
        session.start(mode="probe")
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
    default_target = coerce_target(None, None, None)
    try:
        result = await process_answer(
            question=note_question,
            answer=clean,
            area=default_target["area"],
            category=default_target["category"],
            theme=default_target["theme"],
            context_atoms=context_for_target(default_target["area"], default_target["category"], default_target["theme"]),
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
        apply_processed(result, q_num, when, note_question, clean, target=default_target)
    except Exception:
        log.exception("apply_processed failed in note ingest")
    vault.commit_all(f"ucho note {when.strftime('%Y-%m-%d %H:%M')}")

    reaction = (result.get("reaction") or "").strip() or "Складно. Слишком складно."
    new_n = vault.next_q_num()
    next_target = default_target
    observations = result.get("worldview_observations") or []
    if observations:
        first = observations[0]
        next_target = coerce_target(first.get("area"), first.get("category"), first.get("theme"))
    if s is not None:
        session.set_question(question_field_with_face(reaction, bot_mood), target=next_target, q_num=new_n)
        session.persist()
        mode = s.mode
        session_id = s.id
    else:
        mode = "probe"
        session_id = None
    return NoteReactionPayload(
        q_num=new_n,
        mode=mode,
        area=next_target["area"],
        category=next_target["category"],
        theme=next_target["theme"],
        theme_key=next_target["theme_key"],
        text=reaction,
        bot_mood=bot_mood,
        answered_q_num=q_num,
        answered_question=note_question,
        session_id=session_id,
        user_text=clean,
        session_context=session_context,
    )
