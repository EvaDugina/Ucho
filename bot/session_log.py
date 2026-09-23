"""Append-only полный лог сообщений активной сессии.

`00_raw/sessions/<timestamp>_<uuid>.jsonl` — единственный источник истины: вопросы,
ответы, заметки, реакции и книжные фрагменты.
"""
from __future__ import annotations

import json
import logging
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

from . import userctx
from .atomic import atomic_write_text
from .errors import VaultError

log = logging.getLogger(__name__)
SESSION_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,80}")
UUID_HEX_RE = re.compile(r"[0-9a-f]{32}")
SESSION_FILE_RE = re.compile(r"\d{8}T\d{6}_([0-9a-f]{32})")


def _sessions_dir() -> Path:
    return userctx.user_root() / "00_raw" / "sessions"


def _ts(value: object | None = None) -> str:
    if isinstance(value, datetime):
        return value.isoformat(timespec="seconds")
    if isinstance(value, str) and value:
        return value
    return datetime.now().isoformat(timespec="seconds")


def filename_uuid(session_id: str) -> str:
    """Стабильный UUID файла; исторические session_id внутри raw не меняются."""
    if UUID_HEX_RE.fullmatch(session_id):
        return session_id
    return uuid.uuid5(uuid.NAMESPACE_URL, f"ucho-session:{session_id}").hex


def filename_timestamp(value: object | None = None) -> str:
    """Сортируемая метка первого события: aware-время в UTC, naive как записано."""
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        dt = datetime.fromisoformat(value)
    else:
        dt = datetime.now(timezone.utc)
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y%m%dT%H%M%S")


def timestamped_filename(session_id: str, first_at: object | None = None) -> str:
    return f"{filename_timestamp(first_at)}_{filename_uuid(session_id)}.jsonl"


def _session_file(directory: Path, session_id: str, first_at: object | None) -> Path:
    """Найти канонический timestamp_UUID файл существующей сессии."""
    suffix = f"_{filename_uuid(session_id)}.jsonl"
    matches = [
        path
        for path in directory.glob(f"*{suffix}")
        if SESSION_FILE_RE.fullmatch(path.stem)
    ]
    if len(matches) > 1:
        raise ValueError(f"multiple raw files for session_id={session_id!r}")
    path = matches[0] if matches else directory / timestamped_filename(session_id, first_at)
    if path.is_symlink():
        raise ValueError("raw session path is a symlink")
    return path


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
    """Дописать событие в сортируемый файл сессии."""
    if not session_id or not SESSION_ID_RE.fullmatch(session_id):
        if required:
            raise VaultError("session log append failed: unsafe session_id")
        return None
    try:
        d = _sessions_dir()
        d.mkdir(parents=True, exist_ok=True)
        timestamp = _ts(at)
        path = _session_file(d, session_id, timestamp)
        line_no = 0
        if path.exists():
            line_no = sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
        telegram_mid = int(message_id) if message_id is not None else None
        entry = {
            "event_id": f"{session_id}:{line_no + 1:06d}",
            "ts": timestamp,
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
            f.flush()
            os.fsync(f.fileno())
        try:
            _write_markdown_view(path)
        except Exception:
            log.exception("session Markdown view update failed: %s", path)
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


def _write_markdown_view(raw_path: Path) -> None:
    """Пересоздать читаемую страницу из канонического JSONL."""
    rows = []
    for line in raw_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if not isinstance(row, dict):
            raise ValueError("invalid session event")
        rows.append(row)
    parts = [
        f"# Разговор {raw_path.stem}",
        "",
        "> Автоматическое представление. Источник — одноимённый JSONL; правки этой страницы перезаписываются.",
        "",
    ]
    for row in rows:
        role = "Пользователь" if row.get("role") == "user" else "Ухо"
        timestamp = str(row.get("ts") or "")
        kind = str(row.get("kind") or "")
        parts.extend([f"## {timestamp} · {role}", "", f"*{kind}*", ""])
        message = str(row.get("text") or "")
        parts.extend(f"> {line}" if line else ">" for line in message.splitlines() or [""])
        parts.append("")
    atomic_write_text(raw_path.with_suffix(".md"), "\n".join(parts).rstrip() + "\n")


def rebuild_views() -> int:
    """Восстановить Markdown-страницы всех сессий текущего пользователя."""
    count = 0
    for raw_path in sorted(_sessions_dir().glob("*.jsonl")):
        if raw_path.is_symlink():
            log.error("unsafe session log path: %s", raw_path)
            continue
        try:
            _write_markdown_view(raw_path)
            count += 1
        except (OSError, ValueError, TypeError):
            log.exception("session Markdown view rebuild failed: %s", raw_path)
    return count


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


def latest_answered_session_transcript(*, max_chars: int = 24_000) -> str:
    """История сессии с самым поздним непустым пользовательским событием."""
    candidates: list[tuple[float, int, dict]] = []
    for index, event in enumerate(iter_events()):
        if event.get("role") != "user" or not str(event.get("text") or "").strip():
            continue
        try:
            parsed = datetime.fromisoformat(str(event.get("ts")))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            timestamp = parsed.timestamp()
        except (TypeError, ValueError, OSError):
            timestamp = float("-inf")
        candidates.append((timestamp, index, event))
    if not candidates:
        return ""
    latest = max(candidates, key=lambda item: (item[0], item[1]))[2]
    return transcript(str(latest.get("session_id") or ""), max_chars=max_chars)


def message_ids(session_id: str | None = None) -> list[int]:
    ids: list[int] = []
    events = session_events(session_id) if session_id else iter_events()
    for e in events:
        mid = e.get("telegram_message_id")
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
        if e.get("telegram_message_id") == mid:
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
            "message_id": e.get("telegram_message_id"),
            "q_num": target,
            "text": question_field_text(e),
            "domain": e.get("domain") or "",
            "answered": _is_answered(target),
            "ts": e.get("ts"),
            "session_id": e.get("session_id"),
        }
    if fallback is not None:
        return {
            "message_id": fallback.get("telegram_message_id"),
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
