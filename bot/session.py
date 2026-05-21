"""Активная сессия владельца. Глобальная — пользователь один (whitelist).

Сессия персистится в `<vault>/_session.json` на каждом изменении, чтобы
перезапуск контейнера / рестарт хоста не терял контекст.
"""
import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Literal, Optional

from .atomic import atomic_write_json
from .config import VAULT_PATH

log = logging.getLogger(__name__)

Mode = Literal["probe", "review"]
MAX_HISTORY = 6  # последние N пар user/assistant в LLM-контексте

SESSION_FILE = VAULT_PATH / "_session.json"


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
    # Иерархия вопросов в режиме probe: один главный + до 2 поясняющих + закрывающий комментарий.
    main_question: str = ""
    main_q_num: Optional[int] = None
    clarifier_count: int = 0
    # Двухфазный коммит ответа: записывается ДО process_answer, чистится ПОСЛЕ
    # успешного завершения всех шагов. Если бот падает между этими точками —
    # после рестарта recovery дожимает обработку.
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
        # Не-известные поля (например, после downgrade схемы) тихо отбрасываем;
        # старые JSON без новых полей подхватят дефолты.
        valid = {f for f in cls.__dataclass_fields__}
        d = {k: v for k, v in d.items() if k in valid}
        ts = d.get("asked_at")
        d["asked_at"] = datetime.fromisoformat(ts) if ts else datetime.now()
        return cls(**d)


_active: Optional[Session] = None


def _persist() -> None:
    try:
        if _active is None:
            if SESSION_FILE.exists():
                SESSION_FILE.unlink()
            return
        atomic_write_json(SESSION_FILE, _active.to_dict())
    except Exception:
        log.exception("failed to persist session")


def persist() -> None:
    """Публичный хук — вызывать после прямой мутации полей сессии (вне методов класса)."""
    _persist()


def restore() -> Optional[Session]:
    """Вызывается один раз при старте бота. Если файл сессии есть — поднимает её."""
    global _active
    if not SESSION_FILE.exists():
        return None
    try:
        d = json.loads(SESSION_FILE.read_text(encoding="utf-8"))
        _active = Session.from_dict(d)
        log.info(
            "session restored: mode=%s domain=%s last_q=%s",
            _active.mode,
            _active.domain,
            _active.current_q_num,
        )
        return _active
    except Exception:
        log.exception("failed to restore session, starting fresh")
        return None


def get() -> Optional[Session]:
    return _active


def start(mode: Mode, domain: Optional[str] = None) -> Session:
    global _active
    _active = Session(mode=mode, domain=domain)
    _persist()
    return _active


def clear() -> None:
    global _active
    _active = None
    _persist()


def set_question(question: str, domain: str, q_num: Optional[int] = None) -> None:
    if _active is None:
        return
    _active.last_question = question
    _active.last_domain = domain
    _active.asked_at = datetime.now()
    if q_num is not None:
        _active.current_q_num = q_num
    _persist()
