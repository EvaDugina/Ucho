"""Кольцо последних N сессий пользователя (per-user) — для reply-resume.

Ответив (reply) на любое сообщение прошлой сессии, пользователь продолжает её.
Снапшот = ``session.Session.to_dict()``. Индексом служит само кольцо: у каждого
снапшота есть ``message_ids`` (все id сообщений бота этой сессии). Файл —
``users/<uid>/_sessions.json``, кольцо на ``MAX_SESSIONS`` записей.

Модуль НЕ импортирует ``session`` (работает со словарями) — чтобы не было цикла:
``session`` импортирует ``sessions``.
"""
import json
import logging
from pathlib import Path
from typing import Optional

from . import userctx
from .atomic import atomic_write_json

log = logging.getLogger(__name__)

MAX_SESSIONS = 25


def _file() -> Path:
    return userctx.user_root() / "_sessions.json"


def _load() -> list[dict]:
    f = _file()
    if not f.exists():
        return []
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        log.exception("failed to load sessions ring, treating as empty")
        return []


def _save(items: list[dict]) -> None:
    try:
        atomic_write_json(_file(), items[-MAX_SESSIONS:])
    except Exception:
        log.exception("failed to persist sessions ring")


def snapshot(session_dict: dict) -> None:
    """Положить снапшот сессии в кольцо (заменяя прежний с тем же id)."""
    sid = session_dict.get("id")
    if not sid:
        return
    items = [s for s in _load() if s.get("id") != sid]
    items.append(session_dict)
    _save(items)


def load(session_id: str) -> Optional[dict]:
    for s in reversed(_load()):
        if s.get("id") == session_id:
            return s
    return None


def find_by_message_id(message_id: int) -> Optional[str]:
    """id самой свежей сессии, в чьих message_ids есть это сообщение."""
    mid = int(message_id)
    for s in reversed(_load()):
        if mid in (s.get("message_ids") or []):
            return s.get("id")
    return None
