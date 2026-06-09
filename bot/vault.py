"""Compatibility facade for file-backed vault storage.

Новые реализации лежат в:
- `bot.storage.git` / `transaction` / `layout` / `log`;
- `bot.repositories.raw_repo` / `state_repo`.

Этот модуль оставляет старые imports рабочими, чтобы код можно было
мигрировать по частям без большого поведенческого переписывания.
"""
from __future__ import annotations

from .repositories.raw_repo import (
    _ENTRY_RE,
    append_note,
    append_profile,
    append_raw,
    find_question,
    iter_history,
)
from .repositories.state_repo import (
    _load_state,
    _save_state,
    daily_already_sent,
    daily_record,
    daily_reminder_plan,
    mark_daily_reminder_done,
    mark_daily_reminder_planned,
    mark_daily_sent,
    mark_daily_sent_details,
    next_q_num,
)
from .storage.git import (
    _DEFAULT_GITIGNORE,
    _git,
    _git_available,
    _git_commit,
    _git_head,
    _git_reset_hard,
    _is_git_repo,
    _restore_scope,
    _scope,
    commit_all,
    ensure_git_repo,
)
from .storage.layout import (
    _GRAPH_TEMPLATE,
    _ensure_user_graph_settings,
    ensure_layout,
    general_dir,
    index_file,
    mood_dir,
    notes_dir,
    profile_dir,
    raw_dir,
    state_file,
    worldview_area_dir,
    worldview_atoms_dir,
)
from .storage import log as _log
from .storage.transaction import git_wrap

_LOG_MAX_BYTES = _log._LOG_MAX_BYTES


def _rotate_log_if_large() -> None:
    _log._LOG_MAX_BYTES = _LOG_MAX_BYTES
    _log._rotate_log_if_large()


def append_log(level: str, op: str, details: str = "") -> None:
    _log._LOG_MAX_BYTES = _LOG_MAX_BYTES
    _log.append_log(level, op, details)

__all__ = [
    "_DEFAULT_GITIGNORE",
    "_ENTRY_RE",
    "_GRAPH_TEMPLATE",
    "_LOG_MAX_BYTES",
    "_ensure_user_graph_settings",
    "_git",
    "_git_available",
    "_git_commit",
    "_git_head",
    "_git_reset_hard",
    "_is_git_repo",
    "_load_state",
    "_restore_scope",
    "_rotate_log_if_large",
    "_save_state",
    "_scope",
    "append_log",
    "append_note",
    "append_profile",
    "append_raw",
    "commit_all",
    "daily_already_sent",
    "daily_record",
    "daily_reminder_plan",
    "ensure_git_repo",
    "ensure_layout",
    "general_dir",
    "find_question",
    "git_wrap",
    "index_file",
    "iter_history",
    "mark_daily_reminder_done",
    "mark_daily_reminder_planned",
    "mark_daily_sent",
    "mark_daily_sent_details",
    "mood_dir",
    "next_q_num",
    "notes_dir",
    "profile_dir",
    "raw_dir",
    "state_file",
    "worldview_area_dir",
    "worldview_atoms_dir",
]
