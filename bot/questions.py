"""Кольцо последних заданных ГЛАВНЫХ вопросов (per-user) — источник для /history.

Главный вопрос (ask / echo / requestion / дневной) отличается от реакции и
командного якоря: он уходит через ``_send_question(plain=False)``. Реакции
(`plain=True`) и якоря команд (`_session_reply`) сюда НЕ попадают — поэтому
/history показывает только настоящие вопросы, без реплик-уколов и «(твой портрет)».

Файл ``users/<uid>/_questions.json``, кольцо ``MAX_QUESTIONS`` записей.
Запись: ``{n, domain, text, ts}``.
"""
import json
import logging
from datetime import datetime
from pathlib import Path

from . import userctx
from .atomic import atomic_write_json

log = logging.getLogger(__name__)

MAX_QUESTIONS = 50


def _file() -> Path:
    return userctx.user_root() / "_questions.json"


def _load() -> list[dict]:
    f = _file()
    if not f.exists():
        return []
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        log.exception("failed to load questions ring, treating as empty")
        return []


def record(q_num: int, domain: str, text: str) -> None:
    """Записать заданный главный вопрос. Никогда не роняет вызывающий код."""
    try:
        items = _load()
        items.append({
            "n": int(q_num),
            "domain": domain,
            "text": text,
            "ts": datetime.now().isoformat(timespec="seconds"),
        })
        atomic_write_json(_file(), items[-MAX_QUESTIONS:])
    except Exception:
        log.exception("failed to record question (n=%s)", q_num)


def recent(limit: int = 25) -> list[dict]:
    """Последние ``limit`` главных вопросов (старые → новые)."""
    return _load()[-limit:]
