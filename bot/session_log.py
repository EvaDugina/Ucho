"""Append-only полный лог сообщений активной сессии.

`00_raw/sessions/<session_id>.jsonl` — канонический машинный event-log сессии:
вопросы бота, команды/ответы пользователя и реакции Иуды. `00_raw/qna/*.md`
остаётся человекочитаемой Q&A-проекцией для Obsidian/evidence.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

from . import userctx
from .atomic import atomic_write_text
from .errors import VaultError

log = logging.getLogger(__name__)


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
    bot_mood: str | None = None,
    required: bool = False,
) -> dict | None:
    """Дописать событие сообщения в `00_raw/sessions/<session_id>.jsonl`."""
    if not session_id:
        if required:
            raise VaultError("session log append failed: empty session_id")
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
            # Back-compat: старые runtime-хелперы и тесты ещё читают message_id.
            "message_id": telegram_mid,
            "reply_to_message_id": (
                int(reply_to_message_id) if reply_to_message_id is not None else None
            ),
            "q_num": q_num,
            "domain": domain,
            "bot_mood": bot_mood,
            "text": text or "",
            "source": "telegram",
        }
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


def find_event_by_message_id(
    message_id: int | None,
    *,
    session_id: str | None = None,
    role: str | None = None,
) -> dict | None:
    """Найти session event по Telegram message_id."""
    if message_id is None:
        return None
    mid = int(message_id)
    events = session_events(session_id) if session_id else iter_events()
    for e in reversed(events):
        if role is not None and e.get("role") != role:
            continue
        if e.get("telegram_message_id", e.get("message_id")) == mid:
            return e
    return None


def find_assistant_event_by_message_id(message_id: int | None) -> dict | None:
    """Найти bot-событие, на которое можно повесить/сменить лицо Иуды."""
    if message_id is None:
        return None
    mid = int(message_id)
    for e in reversed(iter_events()):
        if e.get("role") != "assistant":
            continue
        if e.get("telegram_message_id", e.get("message_id")) != mid:
            continue
        if e.get("kind") in {"question", "reaction", "regen", "service"}:
            return e
    return None


def set_event_bot_mood(event_id: str | None, bot_mood: str | None) -> dict | None:
    """Точечная metadata-правка bot_mood у события.

    Основной журнал остаётся append-first, но `/remask` — явная админская
    корректировка выбранной маски уже отправленного bot-сообщения. Текст
    события не переписываем; меняем только поле `bot_mood`.
    """
    if not event_id or ":" not in event_id:
        return None
    session_id, raw_no = event_id.rsplit(":", 1)
    try:
        line_no = int(raw_no)
    except ValueError:
        return None
    path = _sessions_dir() / f"{session_id}.jsonl"
    if not path.exists() or line_no < 1:
        return None
    lines = path.read_text(encoding="utf-8").splitlines()
    idx = line_no - 1
    if idx >= len(lines):
        return None
    try:
        row = json.loads(lines[idx])
    except json.JSONDecodeError:
        return None
    if not isinstance(row, dict):
        return None
    row.setdefault("event_id", event_id)
    row["bot_mood"] = bot_mood
    lines[idx] = json.dumps(row, ensure_ascii=False)
    atomic_write_text(path, "\n".join(lines).rstrip() + "\n")
    return row


def find_question_event_by_q_num(q_num: int | None, *, session_id: str | None = None) -> dict | None:
    if q_num is None:
        return None
    target = int(q_num)
    events = session_events(session_id) if session_id else iter_events()
    for e in reversed(events):
        if e.get("role") == "assistant" and e.get("q_num") == target:
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


def find_question_by_message_id(message_id: int) -> dict | None:
    mid = int(message_id)
    for e in reversed(iter_events()):
        if e.get("role") != "assistant":
            continue
        if e.get("telegram_message_id", e.get("message_id")) != mid:
            continue
        if e.get("kind") not in {"question", "reaction", "service"}:
            continue
        return {
            "message_id": mid,
            "q_num": e.get("q_num"),
            "text": question_field_text(e),
            "domain": e.get("domain") or "",
            "answered": _is_answered(e.get("q_num")),
            "ts": e.get("ts"),
            "session_id": e.get("session_id"),
            "bot_mood": e.get("bot_mood"),
        }
    return None


def find_question_by_q_num(q_num: int) -> dict | None:
    target = int(q_num)
    for e in reversed(iter_events()):
        if e.get("role") != "assistant" or e.get("q_num") != target:
            continue
        return {
            "message_id": e.get("telegram_message_id", e.get("message_id")),
            "q_num": target,
            "text": question_field_text(e),
            "domain": e.get("domain") or "",
            "answered": _is_answered(target),
            "ts": e.get("ts"),
            "session_id": e.get("session_id"),
            "bot_mood": e.get("bot_mood"),
        }
    return None


def recent_questions(limit: int = 25) -> list[dict]:
    out: list[dict] = []
    seen: set[int] = set()
    for e in reversed(iter_events()):
        if e.get("role") != "assistant" or e.get("kind") != "question":
            continue
        qn = e.get("q_num")
        if qn is None or qn in seen:
            continue
        seen.add(int(qn))
        out.append({
            "n": int(qn),
            "domain": e.get("domain") or "",
            "text": e.get("text") or "",
            "ts": e.get("ts"),
        })
        if len(out) >= limit:
            break
    return list(reversed(out))


def question_field_text(event: dict | None) -> str:
    """Текст вопроса/якоря для дальнейшего ответа с выбранной маской.

    В UI маска идёт отдельной HTML-строкой, а в question-field для LLM/raw —
    обычным текстом. Это сохраняет информацию о лице без Telegram-разметки.
    """
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
