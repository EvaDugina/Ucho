"""Git-backed transaction helper for vault writes."""
from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from ..errors import VaultError
from .git import _git_available, _git_commit, _git_head, _is_git_repo, _restore_scope, _scope
from .log import append_log


@contextmanager
def git_wrap(op_name: str) -> Iterator[None]:
    """pre-commit -> write block -> post-commit with rollback on exceptions."""
    if not _git_available() or not _is_git_repo():
        append_log("warn", "git_unavailable", f"op={op_name} ran without safety net")
        yield
        return

    scope, label = _scope()
    pre_sha = _git_commit(f"psycho({label}): before {op_name}", scope=scope) or _git_head()
    try:
        yield
    except Exception as exc:
        append_log("error", op_name, f"failed: {exc!r} — attempting rollback")
        if pre_sha:
            ok = _restore_scope(pre_sha, scope)
            append_log("error", op_name, f"rollback {'ok' if ok else 'FAILED'} to {pre_sha[:8]}")
            if not ok:
                raise VaultError(
                    f"{op_name}: операция упала и git-откат не удался — "
                    "данные могут быть неконсистентны"
                ) from exc
        raise
    else:
        _git_commit(f"psycho({label}): {op_name}", scope=scope)

