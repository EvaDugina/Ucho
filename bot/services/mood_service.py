"""Повторный анализ последней содержательной реплики по явному /about."""
from __future__ import annotations

import logging
from datetime import datetime

from .. import about, llm, mood_file, moods, session_log
from ..atomic import atomic_write_text

log = logging.getLogger(__name__)


def latest_source() -> dict | None:
    candidates = [
        e for e in session_log.iter_events()
        if e.get("role") == "user" and e.get("kind") in {"answer", "note", "message"}
        and str(e.get("text") or "").strip()
        and not str(e["text"]).lstrip().startswith("/")
    ]
    return max(candidates, key=lambda e: moods._event_datetime(e.get("ts")), default=None)


async def refresh_from_latest(*, at: datetime | None = None) -> str:
    """updated / no_data / unavailable; не добавляет фиктивный ход в траекторию."""
    event = latest_source()
    if event is None:
        return "no_data"
    path = mood_file.path()
    previous = path.read_text(encoding="utf-8") if path.exists() else None
    try:
        classified = await llm.classify_mood(
            event["text"], about.render_for_prompt(),
            session_context=session_log.transcript(event["session_id"]),
        )
        # Одна повторная оценка с контекстом, без смешивания с прошлым /about.
        vector = moods.session_mood([classified], prior=moods._to_numeric(classified))
        mood_file.set_current(vector, source_at=event["ts"], analyzed_at=at, trigger="about")
        if not moods.log_turn(
            vector, raw_event_id=event["event_id"], session_id=event["session_id"],
            q_num=event.get("q_num"), at=event["ts"], analyzed_at=at, trigger="about",
        ):
            raise OSError("mood journal write failed")
        return "updated"
    except Exception:
        # Ошибка классификации/записи не должна стирать последнюю успешную оценку.
        if previous is None:
            path.unlink(missing_ok=True)
        elif not path.exists() or path.read_text(encoding="utf-8") != previous:
            atomic_write_text(path, previous)
        log.warning("about mood unavailable; previous mood retained")
        return "unavailable"
