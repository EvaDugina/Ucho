"""Активные сессии пользователей (multi-user).

У каждого пользователя — своя сессия, персистится в его
`<vault>/users/<uid>/_session.json`. В памяти держим `dict[uid → Session]`.
Текущий пользователь определяется через `userctx` (request-scoped contextvar),
поэтому публичный API (`get/start/clear/set_question/persist`) работает с
сессией текущего пользователя без явной передачи uid.
"""
import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Literal, Optional

from . import userctx
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


def start(mode: Mode, domain: Optional[str] = None) -> "Session":
    uid = userctx.current_uid()
    s = Session(mode=mode, domain=domain)
    if uid is not None:
        _active[uid] = s
    _persist()
    return s


def clear() -> None:
    uid = userctx.current_uid()
    if uid is not None:
        _active.pop(uid, None)
    _persist()


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
