from __future__ import annotations

import pytest

from bot import session, userctx, users, vault
from bot.errors import VaultError
from bot.services import deletion_service


def _write(path, text: str = "data\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_delete_current_user_data_only_removes_current_user_and_keeps_registry(as_user):
    current_root = userctx.user_root()
    current_note = current_root / "00_raw" / "notes" / "current.md"
    _write(current_note, "current user data\n")
    session.start(mode="probe", domain="everyday")
    session.set_question("Что забыть?", "everyday", q_num=vault.next_q_num())
    assert (current_root / "_session.json").exists()

    other_uid = as_user + 10_000
    other_note = userctx.root_for(other_uid) / "00_raw" / "notes" / "other.md"
    _write(other_note, "other user data\n")
    users.add_user(other_uid, by=as_user)
    users_before = users.USERS_FILE.read_text(encoding="utf-8")

    result = deletion_service.delete_current_user_data()

    assert result.deleted is True
    assert result.uid == as_user
    assert not current_root.exists()
    assert other_note.exists()
    assert users.USERS_FILE.read_text(encoding="utf-8") == users_before
    assert session.get() is None


def test_delete_current_user_data_requires_user_context(as_user):
    try:
        userctx.clear_user()
        with pytest.raises(VaultError):
            deletion_service.delete_current_user_data()
    finally:
        userctx.set_user(as_user)


def test_delete_current_user_data_commit_is_scoped(as_user):
    if not vault._git_available():
        pytest.skip("git недоступен")

    current_root = userctx.user_root()
    _write(current_root / "00_raw" / "notes" / "to-delete.md", "delete me\n")
    other_uid = as_user + 10_000
    other_note = userctx.root_for(other_uid) / "00_raw" / "notes" / "keep.md"
    _write(other_note, "keep me\n")

    deletion_service.delete_current_user_data()

    sha = vault._git("rev-parse", "HEAD").stdout.strip()
    changed = [
        line.strip()
        for line in vault._git("show", "--name-only", "--format=", sha).stdout.splitlines()
        if line.strip()
    ]
    assert changed
    assert all(line.startswith(f"users/{as_user}/") for line in changed)
    assert not any(line.startswith(f"users/{other_uid}/") for line in changed)
    assert other_note.exists()
