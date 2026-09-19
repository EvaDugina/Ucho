"""Live-портрет пользователя: атомарные дельты и версионируемый синтез.

Ответы остаются в ``00_raw/sessions``. Здесь хранится только проверенная
производная: pending/synthesized дельты и нейтральный внутренний профиль.
"""
from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from . import vault
from .atomic import atomic_write_bytes, atomic_write_json, atomic_write_text
from .config import DAILY_TZ
from .errors import VaultError

log = logging.getLogger(__name__)

ASPECTS = {
    "character",
    "speech",
    "emotional_regulation",
    "relationships",
    "values",
    "motivation",
    "habits",
    "self_image",
    "triggers",
}

PROFILE_FIELDS = (
    "updated", "messages_seen", "register", "tone", "openness", "provocation_tolerance",
)


def _split_profile(profile: str) -> tuple[str, str]:
    text = normalize_profile(profile)
    header = re.match(r"^---\n(.*?)\n---(?:\n|$)", text, flags=re.DOTALL)
    return (header.group(1), text[header.end():].strip()) if header else ("", text)


def has_profile_metadata(profile: str) -> bool:
    header, _ = _split_profile(profile)
    keys = set(re.findall(r"^([a-z_]+):", header, flags=re.MULTILINE))
    return set(PROFILE_FIELDS) <= keys


def profile_body(profile: str) -> str:
    return _split_profile(profile)[1]


def _with_system_metadata(profile: str, *, now: datetime, messages_seen: int) -> str:
    header, body = _split_profile(profile)
    rows = [line for line in header.splitlines()
            if not re.match(r"^(updated|messages_seen):", line)]
    keys = set(re.findall(r"^([a-z_]+):", "\n".join(rows), flags=re.MULTILINE))
    rows.extend(f"{key}: null" for key in PROFILE_FIELDS[2:] if key not in keys)
    header = "\n".join([f"updated: '{now.date().isoformat()}'",
                        f"messages_seen: {messages_seen}", *rows])
    return f"---\n{header}\n---\n\n{body}"


def _root() -> Path:
    return vault.personality_dir()


def deltas_path() -> Path:
    return _root() / "deltas.json"


def path() -> Path:
    return _root() / "about" / "current.md"


def versions_dir() -> Path:
    return _root() / "about" / "versions"


def ensure() -> None:
    versions_dir().mkdir(parents=True, exist_ok=True)
    if not deltas_path().exists():
        atomic_write_json(deltas_path(), {"version": 1, "items": []})


def _load_store() -> dict:
    ensure()
    try:
        data = json.loads(deltas_path().read_text(encoding="utf-8"))
    except Exception:
        log.exception("personality deltas unreadable; using empty store")
        return {"version": 1, "items": []}
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        return {"version": 1, "items": []}
    return data


def _confidence(value: object) -> float:
    try:
        return round(max(0.0, min(1.0, float(value))), 3)
    except (TypeError, ValueError):
        return 0.5


def record_deltas(
    raw: object,
    *,
    raw_event_id: str,
    raw_text: str,
    at: object | None = None,
) -> list[dict]:
    """Сохранить валидные personality-дельты, привязанные к raw event."""
    if not raw_event_id or not isinstance(raw, list):
        return []
    created_at = (
        at.isoformat(timespec="seconds")
        if isinstance(at, datetime)
        else str(at or datetime.now().isoformat(timespec="seconds"))
    )
    accepted: list[dict] = []
    for candidate in raw[:6]:
        if not isinstance(candidate, dict):
            continue
        aspect = str(candidate.get("aspect") or "").strip()
        summary = str(candidate.get("summary") or "").strip()
        quote = str(candidate.get("quote") or "").strip()
        if aspect not in ASPECTS or not summary or not quote:
            continue
        if quote not in raw_text:
            log.warning("personality delta dropped: quote is not verbatim")
            continue
        accepted.append(
            {
                "id": uuid.uuid4().hex,
                "created_at": created_at,
                "raw_event_id": raw_event_id,
                "aspect": aspect,
                "summary": summary[:800],
                "quote": quote[:800],
                "confidence": _confidence(candidate.get("confidence")),
                "status": "pending",
                "synthesized_in": None,
                "synthesized_at": None,
            }
        )
    if not accepted:
        return []
    store = _load_store()
    store["items"].extend(accepted)
    atomic_write_json(deltas_path(), store)
    return accepted


def pending_deltas() -> list[dict]:
    return [
        item
        for item in _load_store()["items"]
        if isinstance(item, dict) and item.get("status") == "pending"
    ]


def normalize_profile(profile: str) -> str:
    """Убрать только внешнюю Markdown-обёртку, не меняя содержимое профиля."""
    text = (profile or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    wrapped = re.fullmatch(
        r"(`{3,}|~{3,})[ \t]*(?:markdown|md)?[ \t]*\n(.*?)\n\1[ \t]*",
        text,
        flags=re.DOTALL | re.IGNORECASE,
    )
    return wrapped.group(2).strip() if wrapped else text


def current_profile() -> str:
    try:
        return normalize_profile(path().read_text(encoding="utf-8")) if path().exists() else ""
    except OSError:
        log.exception("failed to read current personality profile")
        return ""


def save_synthesis(
    profile: str,
    delta_ids: list[str],
    *,
    at: datetime | None = None,
) -> str:
    """Сохранить current+version и лишь затем отметить захваченные дельты."""
    body = normalize_profile(profile)
    if not body:
        raise ValueError("empty synthesized profile")
    now = at or datetime.now(ZoneInfo(DAILY_TZ))
    if now.tzinfo is not None:
        now = now.astimezone(ZoneInfo(DAILY_TZ))
    version_id = now.strftime("%Y-%m-%d_%H-%M-%S")
    version_path = versions_dir() / f"{version_id}.md"
    captured = set(delta_ids)
    store = _load_store()
    # Несколько дельт одного ответа не должны увеличивать счётчик сообщений.
    source_ids = {
        item["raw_event_id"] for item in store["items"]
        if item.get("raw_event_id")
        and (item.get("status") == "synthesized" or item.get("id") in captured)
    }
    body = _with_system_metadata(body, now=now, messages_seen=len(source_ids))
    content = body.rstrip() + "\n"
    for item in store["items"]:
        if item.get("id") in captured and item.get("status") == "pending":
            item["status"] = "synthesized"
            item["synthesized_in"] = version_id
            item["synthesized_at"] = now.isoformat(timespec="seconds")
    targets = (version_path, path(), deltas_path())
    previous = {target: target.read_bytes() if target.exists() else None for target in targets}
    try:
        atomic_write_text(version_path, content)
        atomic_write_text(path(), content)
        atomic_write_json(deltas_path(), store)
    except Exception as exc:
        rollback_errors = []
        for target in reversed(targets):
            try:
                old = previous[target]
                if old is None:
                    target.unlink(missing_ok=True)
                else:
                    atomic_write_bytes(target, old)
            except Exception as rollback_exc:
                rollback_errors.append(rollback_exc)
                log.exception("about rollback failed for %s", target)
        if rollback_errors:
            raise VaultError("about synthesis failed and file rollback was incomplete") from exc
        raise
    return version_id


def render_for_prompt(max_chars: int = 3500) -> str:
    text = current_profile()
    text = re.sub(r"^---.*?---\s*", "", text, flags=re.DOTALL)
    return text[-max_chars:]
