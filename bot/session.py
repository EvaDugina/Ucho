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
from datetime import datetime
from pathlib import Path
from typing import Literal, Optional

from . import sessions, userctx
from .atomic import atomic_write_json
from .config import VAULT_PATH

log = logging.getLogger(__name__)

Mode = Literal["probe", "review"]
MAX_HISTORY = 6  # последние N пар user/assistant в LLM-контексте


def _session_file() -> Path:
    return userctx.user_root() / "_session.json"


@dataclass
class Session:
    mode: Mode
    domain: Optional[str] = None
    last_question: str = ""
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

    def record_assistant(self, text: str) -> None:
        if not text:
            return
        self.history.append({"role": "assistant", "content": text})
        self._trim()
        _persist()

    def record_user(self, text: str) -> None:
        if not text:
            return
        self.history.append({"role": "user", "content": text})
        self._trim()
        _persist()

    def _trim(self) -> None:
        if len(self.history) > MAX_HISTORY * 2:
            self.history = self.history[-MAX_HISTORY * 2:]

    def to_dict(self) -> dict:
        d = asdict(self)
        d["asked_at"] = self.asked_at.isoformat()
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Session":
        valid = {f for f in cls.__dataclass_fields__}
        d = {k: v for k, v in d.items() if k in valid}
        ts = d.get("asked_at")
        d["asked_at"] = datetime.fromisoformat(ts) if ts else datetime.now()
        return cls(**d)


# uid → Session. Текущий uid берётся из userctx.
_active: dict[int, "Session"] = {}

# uid → asyncio.Lock. Сериализует await-растянутые мутации одной сессии.
# Ответы в probe (_handle_probe) НЕ проходят single-flight ratelimit (он только
# у /ask, /about, /ucho), поэтому два быстрых сообщения одного пользователя могут
# переплестись на await-границах (classify_mood/process_answer) и испортить
# порядок history/mood_trajectory. Лок по uid сериализует критическую секцию.
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
    """Сохранить непустую сессию в кольцо (для последующего reply-resume)."""
    if s is None or not (s.history or s.message_ids) or not s.id:
        return
    try:
        sessions.snapshot(s.to_dict())
    except Exception:
        log.exception("failed to snapshot session to ring")


def start(mode: Mode, domain: Optional[str] = None) -> "Session":
    uid = userctx.current_uid()
    _snapshot_to_ring(_active.get(uid) if uid is not None else None)
    s = Session(mode=mode, domain=domain, id=uuid.uuid4().hex)
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
    """Сделать активной сессию из кольца (текущую сперва снапшотим)."""
    uid = userctx.current_uid()
    snap = sessions.load(session_id)
    if snap is None:
        return None
    cur = _active.get(uid) if uid is not None else None
    if cur is not None and cur.id != session_id:
        _snapshot_to_ring(cur)
    s = Session.from_dict(snap)
    if uid is not None:
        _active[uid] = s
    _persist()
    return s


def set_question(question: str, domain: str, q_num: Optional[int] = None) -> None:
    s = get()
    if s is None:
        return
    s.last_question = question
    s.last_domain = domain
    s.asked_at = datetime.now()
    if q_num is not None:
        s.current_q_num = q_num
    _persist()
