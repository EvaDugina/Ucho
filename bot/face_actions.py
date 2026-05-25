"""Action records для лиц Иуды, feedback маски и избранных реплик.

Callback Telegram короткий, поэтому в кнопках живёт только token. Полный контекст
для регенерации и лайков хранится в per-user JSON/JSONL рядом с данными сессии.
"""
from __future__ import annotations

import json
import logging
import secrets
from datetime import datetime
from pathlib import Path
from typing import Optional

from . import moods, userctx
from .atomic import atomic_write_json, atomic_write_text

log = logging.getLogger(__name__)

MAX_ACTIONS = 200


def _root() -> Path:
    return userctx.user_root()


def _actions_file() -> Path:
    return _root() / "_face_actions.json"


def _feedback_file() -> Path:
    return _root() / "_mood_feedback.jsonl"


def _liked_file() -> Path:
    return _root() / "_liked_replies.json"


def _liked_log_file() -> Path:
    return _root() / "_liked_replies_log.jsonl"


def _now(value: object | None = None) -> str:
    if isinstance(value, datetime):
        return value.isoformat(timespec="seconds")
    if isinstance(value, str) and value:
        return value
    return datetime.now().isoformat(timespec="seconds")


def _load_json(path: Path, default):
    if not path.exists():
        return default
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, type(default)) else default
    except Exception:
        log.exception("failed to load %s", path)
        return default


def _load_actions() -> dict:
    return _load_json(_actions_file(), {})


def _save_actions(data: dict) -> None:
    items = sorted(
        [v for v in data.values() if isinstance(v, dict)],
        key=lambda r: r.get("created_at") or "",
    )[-MAX_ACTIONS:]
    atomic_write_json(_actions_file(), {r["token"]: r for r in items if r.get("token")})


def _append_jsonl(path: Path, entry: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    lines.append(json.dumps(entry, ensure_ascii=False))
    atomic_write_text(path, "\n".join(lines) + "\n")


def new_token() -> str:
    return secrets.token_urlsafe(6).replace("-", "_")


def create_action(
    *,
    session_id: str | None,
    q_num: int | None,
    answered_q_num: int | None = None,
    kind: str,
    bot_mood: str | None,
    assistant_text: str,
    user_text: str,
    question: str,
    session_context: str,
    reply_to_user_message_id: int | None = None,
    parent_token: str | None = None,
    at: object | None = None,
) -> str:
    token = new_token()
    rec = {
        "token": token,
        "created_at": _now(at),
        "session_id": session_id,
        "q_num": q_num,
        "answered_q_num": answered_q_num,
        "kind": kind,
        "bot_mood": moods.coerce_bot_mood(bot_mood),
        "assistant_text": assistant_text or "",
        "user_text": user_text or "",
        "question": question or "",
        "session_context": session_context or "",
        "reply_to_user_message_id": reply_to_user_message_id,
        "message_id": None,
        "message_ts": None,
        "parent_token": parent_token,
    }
    data = _load_actions()
    data[token] = rec
    _save_actions(data)
    return token


def set_message(token: str, message_id: int | None, at: object | None = None) -> None:
    data = _load_actions()
    rec = data.get(token)
    if not isinstance(rec, dict):
        return
    rec["message_id"] = int(message_id) if message_id is not None else None
    rec["message_ts"] = _now(at)
    data[token] = rec
    _save_actions(data)


def get_action(token: str | None) -> Optional[dict]:
    if not token:
        return None
    rec = _load_actions().get(token)
    return rec if isinstance(rec, dict) else None


def find_by_message_id(message_id: int | None) -> Optional[dict]:
    if message_id is None:
        return None
    mid = int(message_id)
    for rec in _load_actions().values():
        if isinstance(rec, dict) and rec.get("message_id") == mid:
            return rec
    return None


def record_mood_feedback(token: str, verdict: str, at: object | None = None) -> bool:
    rec = get_action(token)
    if rec is None or verdict not in {"suitable", "unsuitable"}:
        return False
    entry = {
        "ts": _now(at),
        "session_id": rec.get("session_id"),
        "q_num": rec.get("q_num"),
        "message_id": rec.get("message_id"),
        "action_token": token,
        "bot_mood": rec.get("bot_mood"),
        "verdict": verdict,
    }
    _append_jsonl(_feedback_file(), entry)
    return True


def _liked_state() -> dict:
    return _load_json(_liked_file(), {})


def is_liked(token: str | None) -> bool:
    if not token:
        return False
    item = _liked_state().get(token)
    return bool(isinstance(item, dict) and item.get("liked"))


def set_liked(token: str, liked: bool | None = None, at: object | None = None) -> Optional[bool]:
    rec = get_action(token)
    if rec is None:
        return None
    state = _liked_state()
    current = bool(isinstance(state.get(token), dict) and state[token].get("liked"))
    new_value = (not current) if liked is None else bool(liked)
    entry = {
        "liked": new_value,
        "updated_at": _now(at),
        "session_id": rec.get("session_id"),
        "q_num": rec.get("q_num"),
        "assistant_message_id": rec.get("message_id"),
        "reply_to_user_message_id": rec.get("reply_to_user_message_id"),
        "bot_mood": rec.get("bot_mood"),
        "kind": rec.get("kind"),
        "assistant_text": rec.get("assistant_text") or "",
        "user_text": rec.get("user_text") or "",
        "action_token": token,
    }
    state[token] = entry
    atomic_write_json(_liked_file(), state)
    _append_jsonl(_liked_log_file(), {"ts": _now(at), **entry})
    return new_value
