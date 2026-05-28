"""Git plumbing for the vault safety net and scoped per-user commits."""
from __future__ import annotations

import logging
import subprocess
from typing import Optional

from .. import userctx
from ..atomic import atomic_write_text
from ..config import VAULT_PATH

log = logging.getLogger(__name__)

_DEFAULT_GITIGNORE = """\
# Obsidian local state
.obsidian/workspace*.json
.obsidian/cache
.trash/

# Psycho global metadata (not per-user; kept out of per-user commits)
.psycho/

# OS junk
.DS_Store
Thumbs.db
desktop.ini

# Editor tmp
*.tmp
*.swp
"""


def _git(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=str(VAULT_PATH),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=check,
    )


def _git_available() -> bool:
    try:
        subprocess.run(["git", "--version"], capture_output=True, check=True)
        return True
    except (FileNotFoundError, subprocess.CalledProcessError):
        return False


def _is_git_repo() -> bool:
    if not (VAULT_PATH / ".git").exists():
        return False
    try:
        _git("rev-parse", "--is-inside-work-tree")
        return True
    except subprocess.CalledProcessError:
        return False


def ensure_git_repo() -> None:
    """Гарантировать, что vault — git репозиторий."""
    if not _git_available():
        log.warning("git not available — safety net disabled; install git in the container")
        return
    fresh = not _is_git_repo()
    if fresh:
        log.info("initializing git repo in vault: %s", VAULT_PATH)
        _git("init", "-b", "main", check=False)
        _git("config", "user.email", "psycho-bot@local", check=False)
        _git("config", "user.name", "Psycho Bot", check=False)

    gitignore = VAULT_PATH / ".gitignore"
    if not gitignore.exists():
        atomic_write_text(gitignore, _DEFAULT_GITIGNORE)
    else:
        current = gitignore.read_text(encoding="utf-8")
        if ".psycho/" not in current:
            block = "\n# Psycho global metadata (not per-user; kept out of per-user commits)\n.psycho/\n"
            atomic_write_text(gitignore, current.rstrip("\n") + "\n" + block)

    rm = _git("rm", "-r", "--cached", "--ignore-unmatch", ".psycho", check=False)
    if fresh:
        _git("add", "-A", check=False)
        _git("commit", "-m", "psycho(all): init", "--allow-empty", check=False)
    elif (rm.stdout or "").strip():
        _git("add", "-A", check=False)
        _git("commit", "-m", "psycho(all): untrack .psycho", check=False)


def _scope() -> tuple[Optional[str], str]:
    uid = userctx.current_uid()
    if uid is None:
        return None, "all"
    return f"users/{uid}", str(uid)


def _git_head() -> Optional[str]:
    if not _git_available() or not _is_git_repo():
        return None
    try:
        return _git("rev-parse", "HEAD").stdout.strip() or None
    except subprocess.CalledProcessError:
        return None


def _git_commit(
    message: str, scope: Optional[str] = None, allow_empty: bool = False
) -> Optional[str]:
    if not _git_available() or not _is_git_repo():
        return None
    try:
        if scope:
            _git("add", "--", scope)
        else:
            _git("add", "-A")
        args = ["commit", "-m", message]
        if allow_empty:
            args.append("--allow-empty")
        if scope:
            args += ["--", scope]
        _git(*args)
        result = _git("rev-parse", "HEAD")
        sha = result.stdout.strip() or None
        if sha:
            _git_push()
        return sha
    except subprocess.CalledProcessError as exc:
        log.warning("git commit failed (%s): %s", message, exc.stderr.strip())
        return None


def commit_all(message: str, allow_empty: bool = False) -> Optional[str]:
    scope, label = _scope()
    return _git_commit(f"psycho({label}): {message}", scope=scope, allow_empty=allow_empty)


def _git_current_branch() -> Optional[str]:
    try:
        branch = _git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    except subprocess.CalledProcessError:
        return None
    if not branch or branch == "HEAD":
        return None
    return branch


def _git_push(remote: str = "origin") -> bool:
    """Best-effort push after a successful vault commit.

    A local/dev vault may not have a remote yet; that should not break the bot.
    In production, a configured `origin` makes every scoped user commit leave the
    container immediately after it is created.
    """
    if not _git_available() or not _is_git_repo():
        return False
    branch = _git_current_branch()
    if not branch:
        log.warning("git push skipped: detached HEAD")
        return False
    remotes = _git("remote", check=False)
    if remote not in (remotes.stdout or "").split():
        log.warning("git push skipped: remote %s is not configured", remote)
        return False
    upstream = _git(
        "rev-parse",
        "--abbrev-ref",
        "--symbolic-full-name",
        "@{u}",
        check=False,
    )
    try:
        if upstream.returncode == 0:
            _git("push")
        else:
            _git("push", "-u", remote, branch)
        return True
    except subprocess.CalledProcessError as exc:
        log.warning("git push failed: %s", exc.stderr.strip())
        return False


def _git_reset_hard(sha: str) -> bool:
    if not _git_available() or not _is_git_repo() or not sha:
        return False
    try:
        _git("reset", "--hard", sha)
        _git("clean", "-fd", check=False)
        return True
    except subprocess.CalledProcessError as exc:
        log.error("git reset --hard %s failed: %s", sha, exc.stderr.strip())
        return False


def _restore_scope(sha: str, scope: Optional[str]) -> bool:
    if not scope:
        return _git_reset_hard(sha)
    if not _git_available() or not _is_git_repo() or not sha:
        return False
    try:
        _git("checkout", sha, "--", scope)
        _git("clean", "-fd", scope, check=False)
        return True
    except subprocess.CalledProcessError as exc:
        log.error("git restore %s -- %s failed: %s", sha, scope, exc.stderr.strip())
        return False
