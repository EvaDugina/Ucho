"""Удаление рабочей базы текущего пользователя.

Опасная операция держит две границы:
- только request-scoped пользователь из ``userctx``;
- только ровный путь ``<VAULT_PATH>/users/<uid>`` после resolve-проверки.

Это не privacy purge: git history/remote не переписываются.
"""
from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from pathlib import Path

from .. import session, userctx, vault
from ..errors import VaultError

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class DeleteUserDataResult:
    uid: int
    path: Path
    deleted: bool


def confirmation_args(uid: int) -> str:
    return f"УДАЛИТЬ {int(uid)}"


def confirmation_command(uid: int) -> str:
    return f"/leta {confirmation_args(uid)}"


def _current_user_root() -> tuple[int, Path]:
    uid = userctx.current_uid()
    if uid is None:
        raise VaultError("delete_user_data called without current uid")

    target = userctx.root_for(uid)
    expected = userctx.system_root() / "users" / str(uid)
    users_root = userctx.system_root() / "users"
    try:
        target_resolved = target.resolve(strict=False)
        expected_resolved = expected.resolve(strict=False)
        parent_resolved = target.parent.resolve(strict=False)
        users_resolved = users_root.resolve(strict=False)
    except OSError as exc:
        raise VaultError("delete_user_data path resolution failed") from exc

    if target_resolved != expected_resolved or parent_resolved != users_resolved:
        raise VaultError("delete_user_data safety check failed")
    return uid, target


def delete_current_user_data() -> DeleteUserDataResult:
    """Удалить ``users/<uid>`` текущего пользователя и забыть runtime-сессию."""
    uid, target = _current_user_root()
    if not target.exists():
        session.clear()
        vault.append_log("info", "leta_noop", f"uid={uid} data_absent")
        return DeleteUserDataResult(uid=uid, path=target, deleted=False)
    if not target.is_dir() or target.is_symlink():
        raise VaultError("delete_user_data target is not a regular directory")

    with vault.git_wrap("delete user data"):
        shutil.rmtree(target)

    session.clear()
    vault.append_log("warn", "user_data_deleted", f"uid={uid}")
    log.warning("user data deleted: uid=%s", uid)
    return DeleteUserDataResult(uid=uid, path=target, deleted=True)
