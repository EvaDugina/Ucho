"""Append-only полный лог сообщений активной сессии.

Старый `raw/YYYY-MM-DD.md` остаётся человекочитаемым Q/A-источником для evidence.
Здесь пишем машинный JSONL: один файл на сессию, хронологически по мере событий.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

from . import userctx

log = logging.getLogger(__name__)


def _sessions_dir() -> Path:
    return userctx.user_root() / "raw" / "sessions"


def _ts(value: object | None = None) -> str:
    if isinstance(value, datetime):
        return value.isoformat(timespec="seconds")
    if isinstance(value, str) and value:
        return value
    return datetime.now().isoformat(timespec="seconds")


def append(
    *,
    session_id: str | None,
    role: str,
    kind: str,
    text: str,
    at: object | None = None,
    message_id: int | None = None,
    reply_to_message_id: int | None = None,
    q_num: int | None = None,
    domain: str | None = None,
    bot_mood: str | None = None,
) -> None:
    """Дописать событие сообщения в `raw/sessions/<session_id>.jsonl`."""
    if not session_id:
        return
    try:
        d = _sessions_dir()
        d.mkdir(parents=True, exist_ok=True)
        entry = {
            "ts": _ts(at),
            "session_id": session_id,
            "role": role,
            "kind": kind,
            "message_id": int(message_id) if message_id is not None else None,
            "reply_to_message_id": (
                int(reply_to_message_id) if reply_to_message_id is not None else None
            ),
            "q_num": q_num,
            "domain": domain,
            "bot_mood": bot_mood,
            "text": text or "",
        }
        with (d / f"{session_id}.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        log.exception("session log append failed (non-fatal)")
