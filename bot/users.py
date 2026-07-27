"""Реестр доверенных пользователей (multi-user whitelist).

Источники allow-листа:
1. `OWNER_TELEGRAM_ID` — владелец/админ (всегда разрешён).
2. `ALLOWED_TELEGRAM_IDS` (env) — начальный список доверенных.
3. `<vault>/.psycho/users.json` — рантайм-реестр: владелец добавляет/убирает
   через /adduser /removeuser без правки .env и рестарта.

Файл реестра ГЛОБАЛЬНЫЙ (на корне вольта, в `.psycho/`), не per-user.
Хранит и флаг `consent` (показан ли disclaimer о приватности).
"""
from __future__ import annotations

import json
import logging
from datetime import date

from .atomic import atomic_write_json
from .config import ALLOWED_TELEGRAM_IDS, OWNER_TELEGRAM_ID, PSYCHO_META_DIR

log = logging.getLogger(__name__)

USERS_FILE = PSYCHO_META_DIR / "users.json"


def _load() -> dict:
    if USERS_FILE.exists():
        try:
            data = json.loads(USERS_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("users"), list):
                if not isinstance(data.get("removed"), list):
                    data["removed"] = []
                return data
            log.warning("users.json has invalid shape, treating as empty")
        except Exception:
            log.exception("failed to read users.json, treating as empty")
    return {"users": [], "removed": []}


def _save(data: dict) -> None:
    PSYCHO_META_DIR.mkdir(parents=True, exist_ok=True)
    atomic_write_json(USERS_FILE, data)


def _valid_uid(value: object) -> int | None:
    try:
        uid = int(value)
    except (TypeError, ValueError):
        return None
    return uid if 0 < uid <= 10**15 else None


def _registry_ids() -> set[int]:
    result: set[int] = set()
    for item in _load()["users"]:
        uid = _valid_uid(item.get("id")) if isinstance(item, dict) else None
        if uid is not None:
            result.add(uid)
    return result


def _removed_ids() -> set[int]:
    result: set[int] = set()
    for value in _load().get("removed", []):
        uid = _valid_uid(value)
        if uid is not None:
            result.add(uid)
    return result


def allowed_ids() -> set[int]:
    """Все разрешённые id: владелец + env + рантайм-реестр."""
    ids = {OWNER_TELEGRAM_ID}
    ids.update(ALLOWED_TELEGRAM_IDS)
    ids.update(_registry_ids())
    ids.difference_update(_removed_ids())
    ids.add(OWNER_TELEGRAM_ID)
    return ids


def is_allowed(uid: int) -> bool:
    return uid in allowed_ids()


def is_owner(uid: int) -> bool:
    return uid == OWNER_TELEGRAM_ID


def add_user(uid: int, by: int) -> bool:
    """Добавить пользователя в реестр. Возвращает False если уже был."""
    data = _load()
    users = data.setdefault("users", [])
    removed = {
        value
        for value in (_valid_uid(item) for item in data.setdefault("removed", []))
        if value is not None
    }
    was_allowed = uid in allowed_ids()
    removed.discard(uid)
    data["removed"] = sorted(removed)
    if any(
        _valid_uid(item.get("id")) == uid
        for item in users
        if isinstance(item, dict)
    ):
        _save(data)
        return False
    users.append({"id": uid, "added": date.today().isoformat(), "by": by, "consent": False})
    _save(data)
    return not was_allowed


def remove_user(uid: int) -> bool:
    """Убрать из реестра (данные в users/<uid>/ НЕ удаляем). False если не было."""
    data = _load()
    was_allowed = uid in allowed_ids()
    users = data.get("users", [])
    new = [
        item
        for item in users
        if not isinstance(item, dict) or _valid_uid(item.get("id")) != uid
    ]
    removed = {
        value
        for value in (_valid_uid(item) for item in data.setdefault("removed", []))
        if value is not None
    }
    if uid in ALLOWED_TELEGRAM_IDS:
        removed.add(uid)
    if len(new) == len(users) and not was_allowed:
        return False
    data["users"] = new
    data["removed"] = sorted(removed)
    _save(data)
    return was_allowed


def list_users() -> list[dict]:
    by_uid: dict[int, dict] = {}
    for item in _load()["users"]:
        uid = _valid_uid(item.get("id")) if isinstance(item, dict) else None
        if uid is not None:
            by_uid[uid] = {**item, "id": uid}
    return [
        by_uid.get(uid, {"id": uid, "consent": False, "source": "env"})
        for uid in sorted(allowed_ids() - {OWNER_TELEGRAM_ID})
    ]


def has_consent(uid: int) -> bool:
    if is_owner(uid):
        return True
    for u in _load()["users"]:
        if isinstance(u, dict) and _valid_uid(u.get("id")) == uid:
            return bool(u.get("consent"))
    # пользователь из env (не в реестре) — заносим запись лениво при set_consent
    return False


def set_consent(uid: int, value: bool = True) -> None:
    data = _load()
    users = data.setdefault("users", [])
    for u in users:
        if isinstance(u, dict) and _valid_uid(u.get("id")) == uid:
            u["consent"] = value
            _save(data)
            return
    # не было записи (пришёл из env) — создаём
    users.append({"id": uid, "added": date.today().isoformat(), "by": OWNER_TELEGRAM_ID, "consent": value})
    _save(data)
