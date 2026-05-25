"""Восстановимая карта Telegram message_id → вопрос из `00_raw/sessions`.

Файл `_qmap.json` больше не является источником истины и не создаётся: все
данные берутся из канонического session event-log. API оставлен для handlers.
"""
from __future__ import annotations

from typing import Optional

from . import session_log


def append(message_id: int, q_num: int, text: str, domain: str, at=None) -> None:
    """Back-compat no-op: событие вопроса уже записано в session_log."""
    return None


def find_by_message_id(message_id: int) -> Optional[dict]:
    return session_log.find_question_by_message_id(int(message_id))


def find_by_q_num(n: int) -> Optional[dict]:
    return session_log.find_question_by_q_num(int(n))


def mark_answered(q_num: int) -> None:
    """Back-compat no-op: answered выводится по user-событиям session-log."""
    return None
