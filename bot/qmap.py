"""Карта message_id → заданный вопрос (per-user, персистентная).

Источник правды по заданным вопросам, включая НЕотвеченные: raw содержит
только отвеченные Q&A (блок пишется в момент ответа), а заданный-но-не-
отвеченный вопрос живёт только здесь (плюс в чате Telegram). Нужна, чтобы
reply на сообщение-вопрос и `/answer N` могли резолвить вопрос
(текст / домен / q_num) даже после рестарта.

Хранится в `users/<uid>/_qmap.json` как кольцо последних ``MAX_ENTRIES``
записей. Запись:

    {message_id, q_num, text, domain, answered, ts}

``message_id`` стабилен между рестартами, поэтому reply резолвится надёжно.
Старые записи вытесняются — на вытесненный вопрос отвечать уже нельзя
(вызывающий код даёт мягкий отказ).
"""
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from . import userctx
from .atomic import atomic_write_json

log = logging.getLogger(__name__)

MAX_ENTRIES = 50


def _qmap_file() -> Path:
    return userctx.user_root() / "_qmap.json"


def _load() -> list[dict]:
    f = _qmap_file()
    if not f.exists():
        return []
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        log.exception("failed to load qmap, treating as empty")
        return []


def _save(entries: list[dict]) -> None:
    try:
        atomic_write_json(_qmap_file(), entries[-MAX_ENTRIES:])
    except Exception:
        log.exception("failed to persist qmap")


def append(message_id: int, q_num: int, text: str, domain: str) -> None:
    """Записать только что отправленный вопрос. answered=False."""
    entries = _load()
    entries.append({
        "message_id": int(message_id),
        "q_num": int(q_num),
        "text": text,
        "domain": domain,
        "answered": False,
        "ts": datetime.now().isoformat(timespec="seconds"),
    })
    _save(entries)


def find_by_message_id(message_id: int) -> Optional[dict]:
    for e in reversed(_load()):
        if e.get("message_id") == int(message_id):
            return e
    return None


def find_by_q_num(n: int) -> Optional[dict]:
    """Самая свежая запись с этим q_num (вопросы шлются с уникальным q_num)."""
    for e in reversed(_load()):
        if e.get("q_num") == int(n):
            return e
    return None


def mark_answered(q_num: int) -> None:
    """Пометить вопрос(ы) с этим q_num как отвеченные. No-op, если q_num нет
    в карте (например, q_num свободной заметки /ucho)."""
    entries = _load()
    changed = False
    for e in entries:
        if e.get("q_num") == int(q_num) and not e.get("answered"):
            e["answered"] = True
            changed = True
    if changed:
        _save(entries)
