"""Узкий фасад файлового vault.

Канонический пользовательский контент хранится в session-log, mood и
personality. Фасад оставляет единый импорт для runtime-сервисов, но больше не
экспортирует graph/profile/Q&A-проекции.
"""
from __future__ import annotations

from .repositories.state_repo import (
    _load_state,
    _save_state,
    daily_already_sent,
    daily_record,
    daily_reminder_plan,
    mark_daily_reminder_done,
    mark_daily_reminder_planned,
    mark_daily_sent_details,
    next_q_num,
)
from .storage import log as _log
from .storage.git import (
    commit_all,
    ensure_git_repo,
)
from .storage.layout import (
    books_dir,
    ensure_layout,
    mood_dir,
    personality_dir,
    raw_dir,
    sessions_dir,
    state_file,
)
from .storage.transaction import books_git_wrap, git_wrap

_LOG_MAX_BYTES = _log._LOG_MAX_BYTES


def _rotate_log_if_large() -> None:
    _log._LOG_MAX_BYTES = _LOG_MAX_BYTES
    _log._rotate_log_if_large()


def append_log(level: str, op: str, details: str = "") -> None:
    _log._LOG_MAX_BYTES = _LOG_MAX_BYTES
    _log.append_log(level, op, details)


__all__ = [
    "_LOG_MAX_BYTES",
    "_load_state",
    "_rotate_log_if_large",
    "_save_state",
    "append_log",
    "books_dir",
    "books_git_wrap",
    "commit_all",
    "daily_already_sent",
    "daily_record",
    "daily_reminder_plan",
    "ensure_git_repo",
    "ensure_layout",
    "git_wrap",
    "mark_daily_reminder_done",
    "mark_daily_reminder_planned",
    "mark_daily_sent_details",
    "mood_dir",
    "next_q_num",
    "personality_dir",
    "raw_dir",
    "sessions_dir",
    "state_file",
]
