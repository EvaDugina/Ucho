"""Применение personality-дельт к уже сохранённому raw-событию."""
from __future__ import annotations

from datetime import datetime

from .. import about, vault


def apply_processed(
    result: dict,
    *,
    raw_event_id: str,
    original_answer: str,
    at: object | None = None,
) -> int:
    """Сохранить только валидные personality-дельты; raw уже на диске."""
    with vault.git_wrap("apply personality deltas"):
        accepted = about.record_deltas(
            result.get("personality_delta"),
            raw_event_id=raw_event_id,
            raw_text=original_answer,
            at=at if isinstance(at, (datetime, str)) else None,
        )
    return len(accepted)
