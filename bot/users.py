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
            return json.loads(USERS_FILE.read_text(encoding="utf-8"))
        except Exception:
            log.exception("failed to read users.json, treating as empty")
    return {"users": []}


def _save(data: dict) -> None:
    PSYCHO_META_DIR.mkdir(parents=True, exist_ok=True)
    atomic_write_json(USERS_FILE, data)


def _registry_ids() -> set[int]:
    return {int(u["id"]) for u in _load().get("users", []) if "id" in u}


def allowed_ids() -> set[int]:
    """Все разрешённые id: владелец + env + рантайм-реестр."""
    ids = {OWNER_TELEGRAM_ID}
    ids.update(ALLOWED_TELEGRAM_IDS)
    ids.update(_registry_ids())
    return ids


def is_allowed(uid: int) -> bool:
    return uid in allowed_ids()


def is_owner(uid: int) -> bool:
    return uid == OWNER_TELEGRAM_ID


def add_user(uid: int, by: int) -> bool:
    """Добавить пользователя в реестр. Возвращает False если уже был."""
    data = _load()
    users = data.setdefault("users", [])
    if any(int(u.get("id")) == uid for u in users):
        return False
    users.append({"id": uid, "added": date.today().isoformat(), "by": by, "consent": False})
    _save(data)
    return True


def remove_user(uid: int) -> bool:
    """Убрать из реестра (данные в users/<uid>/ НЕ удаляем). False если не было."""
    data = _load()
    users = data.get("users", [])
    new = [u for u in users if int(u.get("id")) != uid]
    if len(new) == len(users):
        return False
    data["users"] = new
    _save(data)
    return True


def list_users() -> list[dict]:
    return list(_load().get("users", []))


def has_consent(uid: int) -> bool:
    if is_owner(uid):
        return True
    for u in _load().get("users", []):
        if int(u.get("id")) == uid:
            return bool(u.get("consent"))
    # пользователь из env (не в реестре) — заносим запись лениво при set_consent
    return False


def set_consent(uid: int, value: bool = True) -> None:
    data = _load()
    users = data.setdefault("users", [])
    for u in users:
        if int(u.get("id")) == uid:
            u["consent"] = value
            _save(data)
            return
    # не было записи (пришёл из env) — создаём
    users.append({"id": uid, "added": date.today().isoformat(), "by": OWNER_TELEGRAM_ID, "consent": value})
    _save(data)


def all_data_user_ids() -> list[int]:
    """id пользователей, у кого есть папка users/<uid>/ (для тикера/self-check)."""
    from .config import VAULT_PATH
    users_dir = VAULT_PATH / "users"
    if not users_dir.exists():
        return []
    return sorted(int(p.name) for p in users_dir.iterdir() if p.is_dir() and p.name.isdigit())
