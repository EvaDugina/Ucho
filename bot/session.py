"""Активные сессии пользователей (multi-user).

У каждого пользователя — своя сессия, персистится в его
`<vault>/users/<uid>/_session.json`. В памяти держим `dict[uid → Session]`.
Текущий пользователь определяется через `userctx` (request-scoped contextvar),
поэтому публичный API (`get/start/clear/set_question/persist`) работает с
сессией текущего пользователя без явной передачи uid.
"""
import asyncio
import json
import logging
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal, Optional
from zoneinfo import ZoneInfo

from . import session_log, userctx
from .atomic import atomic_write_json
from .config import DAILY_TZ, VAULT_PATH
from .worldview_taxonomy import coerce_target, get_area, legacy_domain_target

log = logging.getLogger(__name__)

Mode = Literal["probe", "review"]
SESSION_TRANSCRIPT_MAX_CHARS = 24_000
_TRUNCATION_MARKER = "[TRUNCATED_OLDER_SESSION_MESSAGES]"


def _display_tz():
    try:
        return ZoneInfo(DAILY_TZ)
    except Exception:
        if DAILY_TZ == "Europe/Moscow":
            return timezone(timedelta(hours=3))
        return None


def _coerce_dt(value: object | None, fallback: Optional[datetime] = None) -> datetime:
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
    if dt.tzinfo is not None and tz is not None:
        return dt.astimezone(tz)
    return dt


def _ts_iso(value: object | None = None, fallback: Optional[datetime] = None) -> str:
    return _coerce_dt(value, fallback=fallback).isoformat(timespec="seconds")


def _ts_prompt(value: object | None, fallback: Optional[datetime] = None) -> str:
    return _coerce_dt(value, fallback=fallback).strftime("%Y:%m:%d %H:%M")


def _history_entry(role: str, text: str, at: object | None = None) -> dict:
    return {"role": role, "content": text, "ts": _ts_iso(at)}


def _normalize_history(items: object, fallback: Optional[datetime]) -> list[dict]:
    out: list[dict] = []
    if not isinstance(items, list):
        return out
    for item in items:
        if not isinstance(item, dict):
            continue
        role = item.get("role")
        if role not in ("assistant", "user"):
            continue
        content = item.get("content")
        if not isinstance(content, str) or not content:
            continue
        out.append({
            "role": role,
            "content": content,
            "ts": _ts_iso(item.get("ts") or item.get("timestamp"), fallback=fallback),
        })
    return out


def _session_file() -> Path:
    return userctx.user_root() / "_session.json"


@dataclass
class Session:
    mode: Mode
    area: Optional[str] = None
    category: str = ""
    theme: str = ""
    theme_key: str = ""
    domain: Optional[str] = None
    last_question: str = ""
    last_area: str = ""
    last_category: str = ""
    last_theme: str = ""
    last_theme_key: str = ""
    last_domain: str = ""
    current_q_num: Optional[int] = None
    asked_at: datetime = field(default_factory=datetime.now)
    history: list[dict] = field(default_factory=list)
    pending_review_additions: list[dict] = field(default_factory=list)
    main_question: str = ""
    main_q_num: Optional[int] = None
    clarifier_count: int = 0
    # Двухфазный коммит ответа: ставится ДО process_answer, чистится ПОСЛЕ.
    pending_answer: Optional[str] = None
    pending_answer_event_id: Optional[str] = None
    # Один durable merge-slot для текста, пришедшего пока текущий ответ уже в LLM.
    queued_answer: Optional[dict] = None
    # Идентичность сессии и id всех её сообщений бота — для reply-resume (кольцо).
    id: str = ""
    message_ids: list[int] = field(default_factory=list)
    # Траектория настроения за сессию (per-message векторы classify_mood).
    # Сессия растянута во времени → копим, последнее сообщение весит больше
    # (recency в moods.session_mood). Новый главный вопрос = новая сессия = сброс.
    mood_trajectory: list[dict] = field(default_factory=list)

    def record_mood(self, per_msg: dict) -> None:
        if not isinstance(per_msg, dict) or not per_msg:
            return
        self.mood_trajectory.append(per_msg)
        if len(self.mood_trajectory) > 40:
            self.mood_trajectory = self.mood_trajectory[-40:]
        _persist()

    def add_message_id(self, mid: int) -> None:
        self.message_ids.append(int(mid))
        _persist()

    def record_assistant(self, text: str, at: object | None = None) -> None:
        if not text:
            return
        self.history.append(_history_entry("assistant", text, at=at))
        self._trim()
        _persist()

    def record_user(self, text: str, at: object | None = None) -> None:
        if not text:
            return
        self.history.append(_history_entry("user", text, at=at))
        self._trim()
        _persist()

    def _trim(self) -> None:
        # В runtime больше не режем активную сессию: LLM получает транскрипт
        # целиком через render_transcript(), а там есть отдельный safety-limit.
        return

    def render_transcript(self, max_chars: int = SESSION_TRANSCRIPT_MAX_CHARS) -> str:
        """Текст текущей сессии для LLM: время, роль, весь текст, последняя реплика.

        Формат времени для промпта намеренно человеческий и фиксированный:
        ``YYYY:MM:DD HH:MM``. Если старая история была без ``ts``, используем
        ``asked_at`` как честный fallback вместо выдумывания порядка во времени.
        """
        from_log = session_log.transcript(self.id, max_chars=max_chars)
        if from_log:
            return from_log
        entries = _normalize_history(self.history, fallback=self.asked_at)
        if not entries:
            return ""
        lines: list[str] = []
        last_idx = len(entries) - 1
        for idx, h in enumerate(entries):
            role = h["role"]
            marker = ""
            if idx == last_idx:
                marker = " [LAST_USER_MESSAGE]" if role == "user" else " [LAST_MESSAGE]"
            lines.append(f"[{_ts_prompt(h.get('ts'), fallback=self.asked_at)}] {role}{marker}: {h['content']}")
        text = "\n".join(lines)
        if len(text) <= max_chars:
            return text
        marker = _TRUNCATION_MARKER + "\n"
        keep = max(0, max_chars - len(marker))
        tail = text[-keep:] if keep else ""
        if "\n" in tail:
            tail = tail.split("\n", 1)[1]
        return marker + tail

    def to_dict(self) -> dict:
        d = asdict(self)
        d["asked_at"] = self.asked_at.isoformat()
        # История сообщений живёт в 00_raw/sessions. Runtime-файл хранит только
        # состояние активной сессии и восстановимые id, без полного дублирования
        # переписки.
        d["history"] = []
        d["message_ids"] = session_log.message_ids(self.id)[-50:] or self.message_ids[-50:]
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Session":
        valid = {f for f in cls.__dataclass_fields__}
        d = {k: v for k, v in d.items() if k in valid}
        ts = d.get("asked_at")
        d["asked_at"] = datetime.fromisoformat(ts) if ts else datetime.now()
        d["history"] = _normalize_history(d.get("history"), fallback=d["asked_at"])
        return cls(**d)


# uid → Session. Текущий uid берётся из userctx.
_active: dict[int, "Session"] = {}

# uid → asyncio.Lock. Сериализует await-растянутые мутации одной сессии.
# Ответы в probe идут под single-flight с durable `queued_answer` для досланного
# текста. Лок всё равно нужен: он защищает порядок history/mood_trajectory и
# восстановительные сценарии от переплетения на await-границах.
_locks: dict[int, asyncio.Lock] = {}


def lock_for(uid: Optional[int]) -> asyncio.Lock:
    """Вернуть (создав при первом обращении) per-uid лок мутаций сессии.

    None-uid (миграции/глобальные операции) делят общий лок под ключом -1.
    """
    key = uid if uid is not None else -1
    lk = _locks.get(key)
    if lk is None:
        lk = asyncio.Lock()
        _locks[key] = lk
    return lk


def _persist() -> None:
    """Сохранить/удалить файл сессии ТЕКУЩЕГО пользователя."""
    uid = userctx.current_uid()
    sf = _session_file()
    try:
        s = _active.get(uid) if uid is not None else None
        if s is None:
            if sf.exists():
                sf.unlink()
            return
        sf.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(sf, s.to_dict())
    except Exception:
        log.exception("failed to persist session for uid=%s", uid)


def persist() -> None:
    """Публичный хук — после прямой мутации полей сессии текущего пользователя."""
    _persist()


def restore_all() -> list[tuple[int, "Session"]]:
    """Поднять сессии всех пользователей из `users/<uid>/_session.json`.

    Вызывается один раз при старте. Возвращает [(uid, session)] для логов и
    pending-recovery.
    """
    out: list[tuple[int, Session]] = []
    users_dir = VAULT_PATH / "users"
    if not users_dir.exists():
        return out
    for udir in sorted(users_dir.iterdir()):
        if not udir.is_dir() or not udir.name.isdigit():
            continue
        sf = udir / "_session.json"
        if not sf.exists():
            continue
        try:
            d = json.loads(sf.read_text(encoding="utf-8"))
            s = Session.from_dict(d)
            uid = int(udir.name)
            _active[uid] = s
            out.append((uid, s))
            log.info("session restored: uid=%s mode=%s last_q=%s", uid, s.mode, s.current_q_num)
        except Exception:
            log.exception("failed to restore session from %s", sf)
    return out


def get() -> Optional["Session"]:
    uid = userctx.current_uid()
    return _active.get(uid) if uid is not None else None


def _snapshot_to_ring(s: Optional["Session"]) -> None:
    """Исторический no-op: прошлые сессии восстанавливаются из 00_raw/sessions."""
    return


def _target_from_values(
    *,
    target: Optional[dict] = None,
    area: Optional[str] = None,
    category: Optional[str] = None,
    theme: Optional[str] = None,
    domain: Optional[str] = None,
) -> dict:
    if target:
        return coerce_target(target.get("area"), target.get("category"), target.get("theme"))
    if area and get_area(area):
        return coerce_target(area, category, theme)
    legacy = legacy_domain_target(domain or area)
    if legacy:
        return legacy
    return coerce_target(area, category, theme)


def target_snapshot(s: Optional["Session"] = None) -> dict:
    s = s or get()
    if s is None:
        return coerce_target(None, None, None)
    return _target_from_values(
        area=s.last_area or s.area,
        category=s.last_category or s.category,
        theme=s.last_theme or s.theme,
        domain=s.last_domain or s.domain,
    )


def start(
    mode: Mode,
    *,
    target: Optional[dict] = None,
    area: Optional[str] = None,
    category: Optional[str] = None,
    theme: Optional[str] = None,
    domain: Optional[str] = None,
) -> "Session":
    uid = userctx.current_uid()
    _snapshot_to_ring(_active.get(uid) if uid is not None else None)
    t = _target_from_values(target=target, area=area, category=category, theme=theme, domain=domain)
    s = Session(
        mode=mode,
        area=t["area"],
        category=t["category"],
        theme=t["theme"],
        theme_key=t["theme_key"],
        domain=domain,
        id=uuid.uuid4().hex,
    )
    if uid is not None:
        _active[uid] = s
    _persist()
    return s


def clear() -> None:
    """Убрать активную сессию без снапшота (для аварийного сброса)."""
    uid = userctx.current_uid()
    if uid is not None:
        _active.pop(uid, None)
    _persist()


def close() -> bool:
    """Закрыть активную сессию: снапшот в кольцо + очистка. True, если была."""
    uid = userctx.current_uid()
    s = _active.get(uid) if uid is not None else None
    if s is None:
        return False
    _snapshot_to_ring(s)
    if uid is not None:
        _active.pop(uid, None)
    _persist()
    return True


def resume(session_id: str) -> Optional["Session"]:
    """Сделать активной сессию из канонического session-log."""
    uid = userctx.current_uid()
    events = session_log.session_events(session_id)
    if not events:
        return None
    cur = _active.get(uid) if uid is not None else None
    if cur is not None and cur.id != session_id:
        _snapshot_to_ring(cur)
    last_q = None
    main_q = None
    last_assistant = ""
    last_area = ""
    last_category = ""
    last_theme = ""
    last_theme_key = ""
    last_domain = ""
    asked_at = datetime.now()
    message_ids: list[int] = []
    history: list[dict] = []
    for e in events:
        mid = e.get("telegram_message_id", e.get("message_id"))
        if mid is not None:
            try:
                message_ids.append(int(mid))
            except (TypeError, ValueError):
                pass
        if e.get("role") in ("assistant", "user") and e.get("text"):
            history.append(_history_entry(str(e.get("role")), str(e.get("text")), at=e.get("ts")))
        if e.get("role") == "assistant" and e.get("kind") != "reminder":
            last_assistant = str(e.get("text") or last_assistant)
            last_area = str(e.get("area") or last_area)
            last_category = str(e.get("category") or last_category)
            last_theme = str(e.get("theme") or last_theme)
            last_theme_key = str(e.get("theme_key") or last_theme_key)
            last_domain = str(e.get("domain") or last_domain)
            if e.get("q_num") is not None:
                last_q = int(e["q_num"])
                if e.get("kind") == "question" and main_q is None:
                    main_q = last_q
            try:
                asked_at = _coerce_dt(e.get("ts"))
            except Exception:
                pass
    target = _target_from_values(area=last_area, category=last_category, theme=last_theme, domain=last_domain)
    s = Session(
        mode="probe",
        area=target["area"],
        category=target["category"],
        theme=target["theme"],
        theme_key=target["theme_key"],
        domain=last_domain or None,
        last_question=last_assistant,
        last_area=target["area"],
        last_category=target["category"],
        last_theme=target["theme"],
        last_theme_key=target["theme_key"],
        last_domain=last_domain,
        current_q_num=last_q,
        asked_at=asked_at,
        history=history,
        main_question=last_assistant if main_q == last_q else "",
        main_q_num=main_q,
        id=session_id,
        message_ids=message_ids[-50:],
    )
    if uid is not None:
        _active[uid] = s
    _persist()
    return s


def has_pending(s: Optional["Session"] = None) -> bool:
    s = s or get()
    return bool(s and (s.pending_answer or s.pending_answer_event_id))


def has_queued(s: Optional["Session"] = None) -> bool:
    s = s or get()
    q = s.queued_answer if s is not None else None
    return isinstance(q, dict) and bool(str(q.get("text") or "").strip())


def has_unfinished_answer(s: Optional["Session"] = None) -> bool:
    s = s or get()
    return has_pending(s) or has_queued(s)


def pending_answer_text(s: Optional["Session"] = None) -> str:
    s = s or get()
    if s is None:
        return ""
    if s.pending_answer_event_id:
        event = session_log.find_event(s.pending_answer_event_id)
        text = (event or {}).get("text") if isinstance(event, dict) else ""
        if isinstance(text, str) and text.strip():
            return text.strip()
    return (s.pending_answer or "").strip()


def enqueue_answer(
    text: str,
    *,
    message_id: int | None = None,
    at: object | None = None,
    reply_to_message_id: int | None = None,
    source: str = "text",
) -> Optional[dict]:
    """Склеить текст в один отложенный ответ на текущий вопрос.

    Очередь хранит snapshot вопроса до того, как текущая генерация превратит
    `last_question` в новый комментарий Иуды. Сам текст до старта обработки не
    пишется в `00_raw/sessions`, поэтому `/start` может удалить его без следа.
    """
    clean = (text or "").strip()
    if not clean:
        return None
    s = get()
    if s is None or not s.last_question:
        return None
    fragment = {
        "text": clean,
        "source": source,
        "message_id": message_id,
        "reply_to_message_id": reply_to_message_id,
        "at": _ts_iso(at),
    }
    q = s.queued_answer if isinstance(s.queued_answer, dict) else None
    if q is None or not str(q.get("text") or "").strip():
        q = {
            "text": clean,
            "fragments": [fragment],
            "question": s.last_question,
            "area": s.last_area or s.area,
            "category": s.last_category or s.category,
            "theme": s.last_theme or s.theme,
            "theme_key": s.last_theme_key or s.theme_key,
            "domain": s.last_domain or s.domain,
            "origin_q_num": s.current_q_num,
            "asked_at": s.asked_at.isoformat(),
            "session_id": s.id,
            "mode": s.mode,
            "session_context": s.render_transcript(),
        }
    else:
        existing = str(q.get("text") or "").strip()
        q["text"] = "\n\n".join(x for x in (existing, clean) if x)
        fragments = q.get("fragments")
        if not isinstance(fragments, list):
            fragments = []
        fragments.append(fragment)
        q["fragments"] = fragments
    s.queued_answer = q
    _persist()
    return q


def clear_queued_answer(s: Optional["Session"] = None) -> bool:
    s = s or get()
    if s is None or not has_queued(s):
        return False
    s.queued_answer = None
    _persist()
    return True


def pop_queued_answer(s: Optional["Session"] = None) -> Optional[dict]:
    s = s or get()
    if s is None or not has_queued(s):
        return None
    q = s.queued_answer
    s.queued_answer = None
    _persist()
    return q if isinstance(q, dict) else None


def set_question(
    question: str,
    area: str | None = None,
    category: str | None = None,
    theme: str | None = None,
    q_num: Optional[int] = None,
    *,
    target: Optional[dict] = None,
    domain: Optional[str] = None,
) -> None:
    s = get()
    if s is None:
        return
    t = _target_from_values(target=target, area=area, category=category, theme=theme, domain=domain)
    s.last_question = question
    s.last_area = t["area"]
    s.last_category = t["category"]
    s.last_theme = t["theme"]
    s.last_theme_key = t["theme_key"]
    s.last_domain = domain or (area if area and not get_area(area) else s.last_domain)
    s.asked_at = datetime.now()
    if q_num is not None:
        s.current_q_num = q_num
    _persist()
