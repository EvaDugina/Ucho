"""Durable inbox входящих сообщений пользователя.

Это самый ранний append-only журнал после whitelist/middleware: полный текст
пользовательского сообщения пишется в vault ДО бизнес-логики и LLM. `raw/`
остаётся каноничным Q&A после обработки, но inbox нужен как аварийный чёрный
ящик на случай рестарта между получением Telegram update и ответом Иуды.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from . import userctx

log = logging.getLogger(__name__)


def _inbox_dir() -> Path:
    return userctx.user_root() / "raw" / "inbox"


def _ts(value: object | None = None) -> str:
    if isinstance(value, datetime):
        return value.isoformat(timespec="seconds")
    if isinstance(value, str) and value:
        return value
    return datetime.now().isoformat(timespec="seconds")


def _path_for(ts: str) -> Path:
    day = (ts[:10] if len(ts) >= 10 else datetime.now().strftime("%Y-%m-%d"))
    return _inbox_dir() / f"{day}.jsonl"


def append(
    *,
    kind: str,
    text: str,
    at: object | None = None,
    message_id: int | None = None,
    chat_id: int | None = None,
    reply_to_message_id: int | None = None,
    session_id: str | None = None,
    session_mode: str | None = None,
    q_num: int | None = None,
    domain: str | None = None,
) -> None:
    """Дописать входящее пользовательское событие в per-user inbox."""
    try:
        ts = _ts(at)
        entry = {
            "ts": ts,
            "uid": userctx.current_uid(),
            "chat_id": int(chat_id) if chat_id is not None else None,
            "message_id": int(message_id) if message_id is not None else None,
            "reply_to_message_id": (
                int(reply_to_message_id) if reply_to_message_id is not None else None
            ),
            "kind": kind,
            "session_id": session_id,
            "session_mode": session_mode,
            "q_num": q_num,
            "domain": domain,
            "text": text or "",
            "text_len": len(text or ""),
        }
        path = _path_for(ts)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        log.exception("inbox append failed (non-fatal)")


def iter_entries() -> list[dict[str, Any]]:
    """Прочитать все inbox-события текущего пользователя по порядку записи."""
    root = _inbox_dir()
    if not root.exists():
        return []
    rows: list[dict[str, Any]] = []
    for path in sorted(root.glob("*.jsonl")):
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    log.warning("bad inbox json line in %s", path)
                    continue
                if isinstance(item, dict):
                    rows.append(item)
        except OSError:
            log.exception("failed to read inbox file %s", path)
    return rows


def latest_text_for_session(session_id: str | None) -> dict[str, Any] | None:
    """Последнее текстовое некомандное сообщение для указанной сессии."""
    if not session_id:
        return None
    latest: dict[str, Any] | None = None
    for row in iter_entries():
        if row.get("session_id") != session_id:
            continue
        if row.get("kind") not in {"text", "caption"}:
            continue
        text = (row.get("text") or "").strip()
        if not text:
            continue
        latest = row
    return latest
