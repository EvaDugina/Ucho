"""Action records для лиц Иуды, пользовательских оценок и избранных реплик.

Callback Telegram короткий, поэтому в кнопках живёт только token. Runtime JSON
хранит ссылки на `00_raw/sessions` events, а не копии полного текста.
"""
from __future__ import annotations

import json
import logging
import secrets
from datetime import datetime
from pathlib import Path
from typing import Optional

from . import moods, session_log, vault
from .atomic import atomic_write_json, atomic_write_text

log = logging.getLogger(__name__)

MAX_ACTIONS = 200


def _root() -> Path:
    return vault.general_dir()


def _actions_file() -> Path:
    return _root() / "face_actions.json"


def _feedback_file() -> Path:
    return vault.mood_dir() / "feedback.jsonl"


def _liked_file() -> Path:
    return _root() / "liked_replies.json"


def _liked_log_file() -> Path:
    return _root() / "liked_replies_log.jsonl"


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


def _root_token(data: dict, token: str | None) -> str | None:
    if not token:
        return None
    seen: set[str] = set()
    current = token
    while current and current not in seen:
        seen.add(current)
        rec = data.get(current)
        if not isinstance(rec, dict):
            return current
        explicit = rec.get("root_token")
        parent = rec.get("parent_token")
        if explicit:
            return str(explicit)
        if not parent:
            return str(current)
        current = str(parent)
    return current


def _same_chain(data: dict, rec: dict, root: str | None) -> bool:
    if not root:
        return False
    if rec.get("root_token") == root:
        return True
    return _root_token(data, rec.get("token")) == root


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
    data = _load_actions()
    user_event = session_log.find_event_by_message_id(
        reply_to_user_message_id, session_id=session_id, role="user"
    )
    question_event = session_log.find_question_event_by_q_num(
        answered_q_num or q_num, session_id=session_id
    )
    root_token = _root_token(data, parent_token) if parent_token else token
    rec = {
        "token": token,
        "root_token": root_token,
        "created_at": _now(at),
        "session_id": session_id,
        "q_num": q_num,
        "answered_q_num": answered_q_num,
        "domain": question_event.get("domain") if question_event else None,
        "area": question_event.get("area") if question_event else None,
        "category": question_event.get("category") if question_event else None,
        "theme": question_event.get("theme") if question_event else None,
        "theme_key": question_event.get("theme_key") if question_event else None,
        "kind": kind,
        "bot_mood": moods.coerce_bot_mood(bot_mood),
        "assistant_event_id": None,
        "user_event_id": user_event.get("event_id") if user_event else None,
        "question_event_id": question_event.get("event_id") if question_event else None,
        "reply_to_user_message_id": reply_to_user_message_id,
        "message_id": None,
        "message_ts": None,
        "parent_token": parent_token,
    }
    data[token] = rec
    _save_actions(data)
    return token


def create_remask_action(event: dict, *, parent_token: str | None = None, at: object | None = None) -> str:
    """Создать короткий token для выбора лица у уже отправленного bot-сообщения."""
    token = new_token()
    mid = event.get("telegram_message_id", event.get("message_id"))
    rec = {
        "token": token,
        "root_token": parent_token or token,
        "created_at": _now(at),
        "session_id": event.get("session_id"),
        "q_num": event.get("q_num"),
        "answered_q_num": None,
        "domain": event.get("domain"),
        "area": event.get("area"),
        "category": event.get("category"),
        "theme": event.get("theme"),
        "theme_key": event.get("theme_key"),
        "kind": "remask",
        "bot_mood": event.get("bot_mood"),
        "assistant_event_id": event.get("event_id"),
        "user_event_id": None,
        "question_event_id": event.get("event_id") if event.get("kind") == "question" else None,
        "reply_to_user_message_id": event.get("reply_to_message_id"),
        "message_id": int(mid) if mid is not None else None,
        "message_ts": event.get("ts"),
        "parent_token": parent_token,
    }
    data = _load_actions()
    data[token] = rec
    _save_actions(data)
    return token


def set_bot_mood(token: str, bot_mood: str) -> bool:
    data = _load_actions()
    rec = data.get(token)
    if not isinstance(rec, dict):
        return False
    rec["bot_mood"] = moods.coerce_bot_mood(bot_mood)
    data[token] = rec
    _save_actions(data)
    return True


def set_message(token: str, message_id: int | None, at: object | None = None) -> None:
    data = _load_actions()
    rec = data.get(token)
    if not isinstance(rec, dict):
        return
    rec["message_id"] = int(message_id) if message_id is not None else None
    rec["message_ts"] = _now(at)
    event = session_log.find_event_by_message_id(
        rec["message_id"], session_id=rec.get("session_id"), role="assistant"
    )
    if event:
        rec["assistant_event_id"] = event.get("event_id")
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


def is_rateable(rec: dict | None) -> bool:
    return isinstance(rec, dict) and rec.get("kind") in {"reaction", "regen"}


def used_bot_moods(token: str | None) -> set[str]:
    data = _load_actions()
    root = _root_token(data, token)
    out: set[str] = set()
    for rec in data.values():
        if not isinstance(rec, dict) or not _same_chain(data, rec, root):
            continue
        if rec.get("kind") not in {"reaction", "regen"}:
            continue
        mood = rec.get("bot_mood")
        if mood:
            out.add(moods.coerce_bot_mood(mood))
    return out


def hydrate_action(rec: dict | None) -> dict:
    """Вернуть тексты для regen из event refs.

    Старые records могли хранить тексты напрямую; они остаются fallback только для
    совместимости до вытеснения MAX_ACTIONS.
    """
    if not isinstance(rec, dict):
        return {"question": "", "user_text": "", "assistant_text": "", "session_context": ""}

    def text_from_event(key: str, legacy_key: str) -> str:
        event = session_log.find_event(rec.get(key))
        if event and isinstance(event.get("text"), str):
            return event["text"]
        return rec.get(legacy_key) or ""

    sid = rec.get("session_id")
    return {
        "question": text_from_event("question_event_id", "question"),
        "user_text": text_from_event("user_event_id", "user_text"),
        "assistant_text": text_from_event("assistant_event_id", "assistant_text"),
        "session_context": session_log.transcript(sid) or rec.get("session_context") or "",
    }


def record_user_score(token: str, score: float, reason: str, at: object | None = None) -> bool:
    """Записать явную оценку ответа владельцем.

    `1.0` = отправил в избранное, ответ понравился.
    """
    rec = get_action(token)
    if rec is None or not is_rateable(rec):
        return False
    entry = {
        "ts": _now(at),
        "session_id": rec.get("session_id"),
        "q_num": rec.get("q_num"),
        "message_id": rec.get("message_id"),
        "action_token": token,
        "bot_mood": rec.get("bot_mood"),
        "area": rec.get("area"),
        "category": rec.get("category"),
        "theme": rec.get("theme"),
        "theme_key": rec.get("theme_key"),
        "kind": rec.get("kind"),
        "score": float(score),
        "reason": reason,
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


def set_liked(token: str, liked: bool = True, at: object | None = None) -> Optional[bool]:
    rec = get_action(token)
    if rec is None or not is_rateable(rec):
        return None
    state = _liked_state()
    new_value = bool(liked)
    entry = {
        "liked": new_value,
        "score": 1.0 if new_value else 0.0,
        "updated_at": _now(at),
        "session_id": rec.get("session_id"),
        "q_num": rec.get("q_num"),
        "assistant_message_id": rec.get("message_id"),
        "reply_to_user_message_id": rec.get("reply_to_user_message_id"),
        "bot_mood": rec.get("bot_mood"),
        "area": rec.get("area"),
        "category": rec.get("category"),
        "theme": rec.get("theme"),
        "theme_key": rec.get("theme_key"),
        "kind": rec.get("kind"),
        "assistant_event_id": rec.get("assistant_event_id"),
        "user_event_id": rec.get("user_event_id"),
        "question_event_id": rec.get("question_event_id"),
        "action_token": token,
    }
    state[token] = entry
    atomic_write_json(_liked_file(), state)
    _append_jsonl(_liked_log_file(), {"ts": _now(at), **entry})
    return new_value
