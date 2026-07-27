"""Append-only полный лог сообщений активной сессии.

`00_raw/sessions/<session_id>.jsonl` — единственный источник истины: вопросы,
ответы, заметки, реакции и книжные фрагменты.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from pathlib import Path

from . import userctx
from .errors import VaultError

log = logging.getLogger(__name__)
SESSION_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,80}")


def _sessions_dir() -> Path:
    return userctx.user_root() / "00_raw" / "sessions"


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
    metadata: dict | None = None,
    required: bool = False,
) -> dict | None:
    """Дописать событие сообщения в `00_raw/sessions/<session_id>.jsonl`."""
    if not session_id or not SESSION_ID_RE.fullmatch(session_id):
        if required:
            raise VaultError("session log append failed: unsafe session_id")
        return None
    try:
        d = _sessions_dir()
        d.mkdir(parents=True, exist_ok=True)
        path = d / f"{session_id}.jsonl"
        line_no = 0
        if path.exists():
            line_no = sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
        telegram_mid = int(message_id) if message_id is not None else None
        entry = {
            "event_id": f"{session_id}:{line_no + 1:06d}",
            "ts": _ts(at),
            "session_id": session_id,
            "role": role,
            "kind": kind,
            "telegram_message_id": telegram_mid,
            "reply_to_message_id": (
                int(reply_to_message_id) if reply_to_message_id is not None else None
            ),
            "q_num": q_num,
            "text": text or "",
            "source": "telegram",
            "metadata": dict(metadata or {}),
        }
        if domain is not None:
            entry["domain"] = domain
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return entry
    except Exception as exc:
        if required:
            raise VaultError(f"session log append failed for session_id={session_id!r}") from exc
        log.exception("session log append failed (non-fatal)")
        return None


def append_required(**kwargs) -> dict:
    """Дописать обязательное событие или поднять VaultError.

    Используется для канонических событий, без которых нельзя безопасно
    продолжать LLM-цикл: user-answer перед process и bot question/reaction,
    нужные для reply-resume/recovery.
    """
    event = append(**kwargs, required=True)
    if event is None:
        raise VaultError("session log append failed")
    return event


def iter_events() -> list[dict]:
    """Все события текущего пользователя по порядку файлов/строк."""
    d = _sessions_dir()
    if not d.exists():
        return []
    out: list[dict] = []
    for path in sorted(d.glob("*.jsonl")):
        try:
            for idx, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    log.warning("bad session log json in %s:%s", path, idx)
                    continue
                if not isinstance(row, dict):
                    continue
                row.setdefault("session_id", path.stem)
                row.setdefault("event_id", f"{row['session_id']}:{idx:06d}")
                if "telegram_message_id" not in row:
                    row["telegram_message_id"] = row.get("message_id")
                out.append(row)
        except OSError:
            log.exception("failed to read session log %s", path)
    return out


def session_events(session_id: str | None) -> list[dict]:
    if not session_id:
        return []
    return [e for e in iter_events() if e.get("session_id") == session_id]


def find_event(event_id: str | None) -> dict | None:
    if not event_id:
        return None
    for e in iter_events():
        if e.get("event_id") == event_id:
            return e
    return None


def transcript(session_id: str | None, *, max_chars: int = 24_000) -> str:
    """LLM-friendly transcript из event-log сессии."""
    events = [e for e in session_events(session_id) if e.get("role") in {"assistant", "user"}]
    if not events:
        return ""
    lines: list[str] = []
    for idx, e in enumerate(events):
        role = e.get("role")
        marker = ""
        if idx == len(events) - 1:
            marker = " [LAST_USER_MESSAGE]" if role == "user" else " [LAST_MESSAGE]"
        try:
            ts = datetime.fromisoformat(str(e.get("ts"))).strftime("%Y:%m:%d %H:%M")
        except ValueError:
            ts = datetime.now().strftime("%Y:%m:%d %H:%M")
        text = question_field_text(e) if role == "assistant" else (e.get("text") or "")
        lines.append(f"[{ts}] {role}{marker}: {text}")
    text = "\n".join(lines)
    if len(text) <= max_chars:
        return text
    marker = "[TRUNCATED_OLDER_SESSION_MESSAGES]\n"
    keep = max(0, max_chars - len(marker))
    tail = text[-keep:] if keep else ""
    if "\n" in tail:
        tail = tail.split("\n", 1)[1]
    return marker + tail


def message_ids(session_id: str | None = None) -> list[int]:
    ids: list[int] = []
    events = session_events(session_id) if session_id else iter_events()
    for e in events:
        mid = e.get("telegram_message_id", e.get("message_id"))
        if mid is None:
            continue
        try:
            ids.append(int(mid))
        except (TypeError, ValueError):
            continue
    return ids


def find_session_by_message_id(message_id: int) -> str | None:
    mid = int(message_id)
    for e in reversed(iter_events()):
        if e.get("telegram_message_id", e.get("message_id")) == mid:
            sid = e.get("session_id")
            return str(sid) if sid else None
    return None


def find_question_by_q_num(q_num: int) -> dict | None:
    target = int(q_num)
    fallback: dict | None = None
    for e in reversed(iter_events()):
        if e.get("role") != "assistant" or e.get("q_num") != target:
            continue
        if e.get("kind") == "reminder":
            continue
        if e.get("kind") != "question" and fallback is None:
            fallback = e
            continue
        if e.get("kind") != "question":
            continue
        return {
            "message_id": e.get("telegram_message_id", e.get("message_id")),
            "q_num": target,
            "text": question_field_text(e),
            "domain": e.get("domain") or "",
            "answered": _is_answered(target),
            "ts": e.get("ts"),
            "session_id": e.get("session_id"),
        }
    if fallback is not None:
        return {
            "message_id": fallback.get("telegram_message_id", fallback.get("message_id")),
            "q_num": target,
            "text": question_field_text(fallback),
            "domain": fallback.get("domain") or "",
            "answered": _is_answered(target),
            "ts": fallback.get("ts"),
            "session_id": fallback.get("session_id"),
        }
    return None


def question_field_text(event: dict | None) -> str:
    """Текст вопроса или реакции, служащий якорем следующего ответа."""
    if not isinstance(event, dict):
        return ""
    return str(event.get("text") or "")


def _is_answered(q_num: object) -> bool:
    if q_num is None:
        return False
    try:
        target = int(q_num)
    except (TypeError, ValueError):
        return False
    for e in iter_events():
        if e.get("role") == "user" and e.get("q_num") == target and (e.get("text") or "").strip():
            return True
    return False
