"""Восстановимый индекс сессий поверх `00_raw/sessions`.

`_sessions.json` больше не является источником истины и не создаётся. Полный
транскрипт сессии живёт в `00_raw/sessions/<session_id>.jsonl`; этот модуль
оставляет старый API для reply-resume и тестов.
"""
from __future__ import annotations

from typing import Optional

from . import session_log


def snapshot(session_dict: dict) -> None:
    """Исторический no-op: снапшоты выводятся из event-log."""
    return None


def load(session_id: str) -> Optional[dict]:
    events = session_log.session_events(session_id)
    if not events:
        return None
    message_ids = [
        int(e["telegram_message_id"])
        for e in events
        if isinstance(e.get("telegram_message_id"), int)
    ]
    last_question = ""
    current_q_num = None
    last_area = ""
    last_category = ""
    last_theme = ""
    last_theme_key = ""
    last_domain = None
    asked_at = None
    history: list[dict] = []
    for e in events:
        role = e.get("role")
        text = (e.get("text") or "").strip()
        if role in {"user", "assistant"} and text:
            history.append({"role": role, "content": text, "ts": e.get("ts")})
        if role == "assistant" and e.get("kind") in {"question", "reaction", "service"}:
            last_question = text
            current_q_num = e.get("q_num")
            last_area = e.get("area") or last_area
            last_category = e.get("category") or last_category
            last_theme = e.get("theme") or last_theme
            last_theme_key = e.get("theme_key") or last_theme_key
            last_domain = e.get("domain") or last_domain
            asked_at = e.get("ts") or asked_at
    return {
        "id": session_id,
        "mode": "probe",
        "domain": last_domain,
        "area": last_area,
        "category": last_category,
        "theme": last_theme,
        "theme_key": last_theme_key,
        "last_domain": last_domain,
        "last_area": last_area,
        "last_category": last_category,
        "last_theme": last_theme,
        "last_theme_key": last_theme_key,
        "last_question": last_question,
        "current_q_num": current_q_num,
        "asked_at": asked_at,
        "message_ids": message_ids[-50:],
        "history": history[-12:],
    }


def find_by_message_id(message_id: int) -> Optional[str]:
    return session_log.find_session_by_message_id(message_id)
