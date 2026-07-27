"""Свободная заметка `/ucho` через единый raw/mood/personality pipeline."""
from __future__ import annotations

from datetime import datetime

from .. import session, vault
from .conversation_service import ReactionPayload, process_probe_answer

NoteReactionPayload = ReactionPayload


async def ingest_note(
    clean: str,
    *,
    at: datetime | None = None,
    message_id: int | None = None,
    metadata: dict | None = None,
) -> NoteReactionPayload | None:
    if session.get() is None:
        session.start(domain="everyday")
    current = session.get()
    if current is None:
        return None
    q_num = vault.next_q_num()
    question = "(свободная заметка)"
    session.set_question(question, "everyday", q_num=q_num)
    return await process_probe_answer(
        clean,
        at=at,
        question=question,
        domain_hint="everyday",
        q_num=q_num,
        event_kind="note",
        message_id=message_id,
        metadata={"source": "ucho", **(metadata or {})},
    )
