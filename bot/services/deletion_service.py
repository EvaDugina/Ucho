"""Сброс рабочей базы текущего пользователя.

Опасная операция держит две границы:
- только request-scoped пользователь из ``userctx``;
- только ровный корень ``<VAULT_PATH>/users/<uid>`` после resolve-проверки.

Корень пользователя, whitelist и git history остаются на месте: это reset базы,
а не privacy purge.
"""
from __future__ import annotations

import json
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
    cleared: bool


def confirmation_args(uid: int) -> str:
    return f"УДАЛИТЬ {int(uid)}"


def confirmation_command(uid: int) -> str:
    return f"/leta {confirmation_args(uid)}"


def collect_chat_message_ids(
    *,
    extra_ids: list[int | None] | None = None,
    fill_until_message_id: int | None = None,
    fill_window: int = 300,
) -> list[int]:
    """Собрать Telegram message_id текущего пользователя для best-effort chat purge.

    Bot API не умеет "очистить историю чата" одним вызовом и не удаляет старые
    сообщения. Поэтому собираем известные ids из session-log и добавляем узкое
    окно перед текущей командой: туда попадают сама `/leta`, предыдущее
    предупреждение с точной фразой и другие недавние служебные сообщения.
    """
    ids: set[int] = set()
    root = userctx.user_root()
    sessions_dir = root / "00_raw" / "sessions"
    if sessions_dir.exists():
        for path in sorted(sessions_dir.glob("*.jsonl")):
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except OSError:
                log.exception("failed to read session log for chat purge: %s", path)
                continue
            for line in lines:
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(row, dict):
                    continue
                for key in ("telegram_message_id", "message_id", "reply_to_message_id"):
                    value = row.get(key)
                    if value is not None:
                        try:
                            ids.add(int(value))
                        except (TypeError, ValueError):
                            pass

    if extra_ids:
        for value in extra_ids:
            if value is not None:
                ids.add(int(value))

    if fill_until_message_id is not None and fill_window > 0:
        end = int(fill_until_message_id)
        start = max(1, end - int(fill_window) + 1)
        ids.update(range(start, end + 1))

    return sorted(ids)


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


def _clear_directory_contents(target: Path) -> None:
    for child in target.iterdir():
        if child.is_symlink() or child.is_file():
            child.unlink()
        elif child.is_dir():
            shutil.rmtree(child)
        else:
            raise VaultError(f"delete_user_data unsupported path type: {child}")


def delete_current_user_data() -> DeleteUserDataResult:
    """Очистить содержимое ``users/<uid>``, сохранить корень и забыть runtime-сессию."""
    uid, target = _current_user_root()
    if not target.exists():
        with vault.git_wrap("reset user data"):
            vault.ensure_layout()
        session.clear()
        vault.append_log("warn", "user_data_reset", f"uid={uid} data_absent")
        log.warning("user data reset from absent root: uid=%s", uid)
        return DeleteUserDataResult(uid=uid, path=target, cleared=True)
    if not target.is_dir() or target.is_symlink():
        raise VaultError("delete_user_data target is not a regular directory")

    had_contents = any(target.iterdir())
    with vault.git_wrap("reset user data"):
        _clear_directory_contents(target)
        vault.ensure_layout()

    session.clear()
    vault.append_log("warn", "user_data_reset", f"uid={uid}")
    log.warning("user data reset: uid=%s", uid)
    return DeleteUserDataResult(uid=uid, path=target, cleared=had_contents)
