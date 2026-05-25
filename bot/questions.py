"""Последние главные вопросы, восстановимые из `00_raw/sessions`."""
from __future__ import annotations

from . import session_log


def record(q_num: int, domain: str, text: str) -> None:
    """Back-compat no-op: вопрос уже записан как assistant/question event."""
    return None


def recent(limit: int = 25) -> list[dict]:
    return session_log.recent_questions(limit)
