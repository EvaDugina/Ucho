"""Restart-safe per-user сессии поверх канонического session-log."""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from . import session_log, userctx
from .atomic import atomic_write_json
from .config import DAILY_TZ, VAULT_PATH

log = logging.getLogger(__name__)

SESSION_TRANSCRIPT_MAX_CHARS = 24_000


def _display_tz():
    try:
        return ZoneInfo(DAILY_TZ)
    except Exception:
        return timezone(timedelta(hours=3)) if DAILY_TZ == "Europe/Moscow" else None


def _coerce_dt(value: object | None, fallback: datetime | None = None) -> datetime:
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str) and value:
        try:
            dt = datetime.fromisoformat(value)
        except ValueError:
            dt = fallback or datetime.now()
    else:
        dt = fallback or datetime.now()
    tz = _display_tz()
    return dt.astimezone(tz) if dt.tzinfo is not None and tz is not None else dt


def _ts_iso(value: object | None = None) -> str:
    return _coerce_dt(value).isoformat(timespec="seconds")


def _session_file() -> Path:
    return userctx.user_root() / "_session.json"


@dataclass
class Session:
    domain: Optional[str] = None
    last_question: str = ""
    last_domain: str = ""
    current_q_num: Optional[int] = None
    main_question: str = ""
    pending_answer: Optional[str] = None
    pending_answer_event_id: Optional[str] = None
    queued_answer: Optional[dict] = None
    id: str = ""
    message_ids: list[int] = field(default_factory=list)
    mood_trajectory: list[dict] = field(default_factory=list)
    question_metadata: dict = field(default_factory=dict)

    def record_mood(self, per_msg: dict) -> None:
        if isinstance(per_msg, dict) and per_msg:
            self.mood_trajectory = (self.mood_trajectory + [per_msg])[-40:]
            _persist()

    def add_message_id(self, mid: int) -> None:
        self.message_ids = (self.message_ids + [int(mid)])[-50:]
        _persist()

    def render_transcript(self, max_chars: int = SESSION_TRANSCRIPT_MAX_CHARS) -> str:
        return session_log.transcript(self.id, max_chars=max_chars)

    def to_dict(self) -> dict:
        data = asdict(self)
        data["message_ids"] = session_log.message_ids(self.id)[-50:] or self.message_ids[-50:]
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "Session":
        if not isinstance(data, dict):
            raise ValueError("session root must be an object")
        valid = set(cls.__dataclass_fields__)
        clean = {key: value for key, value in data.items() if key in valid}
        current = cls(**clean)
        if not session_log.SESSION_ID_RE.fullmatch(current.id):
            raise ValueError("unsafe session id")
        return current


_active: dict[int, Session] = {}
_locks: dict[int, asyncio.Lock] = {}


def lock_for(uid: Optional[int]) -> asyncio.Lock:
    key = uid if uid is not None else -1
    if key not in _locks:
        _locks[key] = asyncio.Lock()
    return _locks[key]


def _persist() -> None:
    uid = userctx.current_uid()
    path = _session_file()
    try:
        current = _active.get(uid) if uid is not None else None
        if current is None:
            if path.exists():
                path.unlink()
            return
        atomic_write_json(path, current.to_dict())
    except Exception:
        log.exception("failed to persist session for uid=%s", uid)


def persist() -> None:
    _persist()


def restore_all() -> list[tuple[int, Session]]:
    restored: list[tuple[int, Session]] = []
    users_dir = VAULT_PATH / "users"
    if not users_dir.exists():
        return restored
    for directory in sorted(users_dir.iterdir()):
        path = directory / "_session.json"
        if (
            not directory.is_dir()
            or directory.is_symlink()
            or not directory.name.isdigit()
            or not path.exists()
            or path.is_symlink()
        ):
            continue
        try:
            current = Session.from_dict(json.loads(path.read_text(encoding="utf-8")))
            uid = int(directory.name)
            _active[uid] = current
            restored.append((uid, current))
        except Exception:
            log.exception("failed to restore session from %s", path)
    return restored


def get() -> Optional[Session]:
    uid = userctx.current_uid()
    return _active.get(uid) if uid is not None else None


def start(domain: Optional[str] = None) -> Session:
    uid = userctx.current_uid()
    current = Session(domain=domain, id=uuid.uuid4().hex)
    if uid is not None:
        _active[uid] = current
    _persist()
    return current


def clear() -> None:
    uid = userctx.current_uid()
    if uid is not None:
        _active.pop(uid, None)
    _persist()


def resume(session_id: str) -> Optional[Session]:
    events = session_log.session_events(session_id)
    if not events:
        return None
    last = next(
        (
            event
            for event in reversed(events)
            if event.get("role") == "assistant"
            and event.get("kind") in {"question", "book_question", "reaction"}
        ),
        None,
    )
    if last is None:
        return None
    main = next(
        (
            event
            for event in events
            if event.get("role") == "assistant"
            and event.get("kind") in {"question", "book_question"}
        ),
        last,
    )
    current = Session(
        domain=str(last.get("domain") or "") or None,
        last_question=str(last.get("text") or ""),
        last_domain=str(last.get("domain") or ""),
        current_q_num=int(last["q_num"]) if last.get("q_num") is not None else None,
        main_question=str(main.get("text") or ""),
        id=session_id,
        message_ids=session_log.message_ids(session_id)[-50:],
        question_metadata=dict(last.get("metadata") or {}),
    )
    uid = userctx.current_uid()
    if uid is not None:
        _active[uid] = current
    _persist()
    return current


def has_pending(value: Optional[Session] = None) -> bool:
    current = value or get()
    return bool(current and (current.pending_answer or current.pending_answer_event_id))


def has_queued(value: Optional[Session] = None) -> bool:
    current = value or get()
    queued = current.queued_answer if current else None
    return isinstance(queued, dict) and bool(str(queued.get("text") or "").strip())


def has_unfinished_answer(value: Optional[Session] = None) -> bool:
    return has_pending(value) or has_queued(value)


def pending_answer_text(value: Optional[Session] = None) -> str:
    current = value or get()
    if current is None:
        return ""
    if current.pending_answer_event_id:
        event = session_log.find_event(current.pending_answer_event_id)
        if isinstance(event, dict) and str(event.get("text") or "").strip():
            return str(event["text"]).strip()
    return str(current.pending_answer or "").strip()


def enqueue_answer(
    text: str,
    *,
    message_id: int | None = None,
    at: object | None = None,
    reply_to_message_id: int | None = None,
    source: str = "text",
) -> Optional[dict]:
    clean = (text or "").strip()
    current = get()
    if not clean or current is None or not current.last_question:
        return None
    fragment = {
        "text": clean,
        "source": source,
        "message_id": message_id,
        "reply_to_message_id": reply_to_message_id,
        "at": _ts_iso(at),
    }
    queued = current.queued_answer if isinstance(current.queued_answer, dict) else None
    if queued is None or not str(queued.get("text") or "").strip():
        queued = {
            "text": clean,
            "fragments": [fragment],
            "question": current.last_question,
            "domain": current.last_domain or current.domain,
            "origin_q_num": current.current_q_num,
            "session_id": current.id,
            "session_context": current.render_transcript(),
            "metadata": current.question_metadata,
        }
    else:
        queued["text"] = f"{queued['text']}\n\n{clean}"
        queued.setdefault("fragments", []).append(fragment)
    current.queued_answer = queued
    _persist()
    return queued


def pop_queued_answer(value: Optional[Session] = None) -> Optional[dict]:
    current = value or get()
    if current is None or not has_queued(current):
        return None
    queued = current.queued_answer
    current.queued_answer = None
    _persist()
    return queued if isinstance(queued, dict) else None


def set_question(
    question: str,
    domain: Optional[str] = None,
    q_num: Optional[int] = None,
    *,
    metadata: Optional[dict] = None,
) -> None:
    current = get()
    if current is None:
        return
    current.last_question = question
    current.last_domain = domain or ""
    current.domain = domain
    current.question_metadata = dict(metadata or {})
    if q_num is not None:
        current.current_q_num = q_num
    _persist()
