"""Минимальная файловая схема пользователя и общей библиотеки.

Публичные данные пользователя ограничены тремя каталогами: ``00_raw``,
``01_mood`` и ``02_personality``. Скрытые ``_state.json``/``_session.json``
остаются runtime-метаданными, а ``books/`` живёт глобально на корне vault.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from .. import userctx
from ..config import BOOKS_PATH, LOG_PATH, META_DIR, VAULT_PATH

log = logging.getLogger(__name__)


def raw_dir() -> Path:
    return userctx.user_root() / "00_raw"


def sessions_dir() -> Path:
    return raw_dir() / "sessions"


def mood_dir() -> Path:
    return userctx.user_root() / "01_mood"


def personality_dir() -> Path:
    return userctx.user_root() / "02_personality"


def books_dir() -> Path:
    return BOOKS_PATH


def state_file() -> Path:
    return userctx.user_root() / "_state.json"


def ensure_layout() -> None:
    """Создать только действующую схему текущего пользователя."""
    legacy_meta = VAULT_PATH / ".psycho"
    if legacy_meta.is_dir() and not legacy_meta.is_symlink() and not META_DIR.exists():
        os.rename(legacy_meta, META_DIR)
    sessions_dir().mkdir(parents=True, exist_ok=True)
    (mood_dir() / "events").mkdir(parents=True, exist_ok=True)
    (personality_dir() / "about" / "versions").mkdir(parents=True, exist_ok=True)
    books_dir().mkdir(parents=True, exist_ok=True)
    META_DIR.mkdir(parents=True, exist_ok=True)
    if not LOG_PATH.exists():
        LOG_PATH.write_text("# Operation log\n\n", encoding="utf-8")
