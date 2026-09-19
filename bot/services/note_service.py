"""Свободная заметка `/ucho` через единый raw/mood/personality pipeline."""
from __future__ import annotations

from datetime import datetime

from .. import session
from .conversation_service import ReactionPayload, process_probe_answer

NoteReactionPayload = ReactionPayload


async def ingest_note(
    clean: str,
    *,
    at: datetime | None = None,
    message_id: int | None = None,
    metadata: dict | None = None,
) -> NoteReactionPayload | None:
    # `/ucho` начинает отдельный контекст, даже если до него был активный вопрос.
    session.start(domain="everyday")
    return await process_probe_answer(
        clean,
        at=at,
        question="(свободная заметка)",
        domain_hint="everyday",
        event_kind="note",
        message_id=message_id,
        metadata={"source": "ucho", **(metadata or {})},
    )
