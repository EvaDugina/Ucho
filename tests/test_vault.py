"""Юнит-тесты хранилища: сквозная нумерация, атомарная запись, git-транзакция."""
from __future__ import annotations

import subprocess
from datetime import datetime

import pytest

from bot import graph, userctx, vault
from bot.atomic import atomic_write_text
from bot.config import LOG_PATH, VAULT_PATH
from bot.errors import ValidationError, VaultError
from bot.graph import Concept
from bot.storage import git as git_storage


def test_next_q_num_monotonic(as_user):
    a = vault.next_q_num()
    b = vault.next_q_num()
    assert b == a + 1


def test_runtime_repo_requires_user_context(as_user):
    try:
        userctx.clear_user()
        with pytest.raises(RuntimeError, match="userctx.user_root"):
            vault.next_q_num()
        assert userctx.system_root() == VAULT_PATH
        assert userctx.root_for(as_user) == VAULT_PATH / "users" / str(as_user)
    finally:
        userctx.set_user(as_user)


def test_atomic_write_text_roundtrip(tmp_path):
    p = tmp_path / "sub" / "f.txt"
    atomic_write_text(p, "привет\n")
    assert p.read_text(encoding="utf-8") == "привет\n"
    # перезапись существующего файла
    atomic_write_text(p, "второй")
    assert p.read_text(encoding="utf-8") == "второй"


def _concept(slug, summary="нечто простое для теста о жизни смысле и долге"):
    return Concept(
        slug=slug, name="Проба", type="value", domain="ethics", summary=summary, status="draft"
    )


def test_git_wrap_commits_on_success(as_user):
    if not vault._git_available():
        pytest.skip("git недоступен")
    with vault.git_wrap("unit_commit"):
        assert graph.save_concept(_concept("proba")) is not None
    assert graph._path_for("proba", "ethics").exists()
    log_out = vault._git("log", "--oneline", "-5").stdout
    assert "unit_commit" in log_out


def test_ensure_git_repo_sets_missing_author_identity(as_user, monkeypatch):
    if not vault._git_available():
        pytest.skip("git недоступен")

    monkeypatch.delenv("VAULT_GIT_USER_NAME", raising=False)
    monkeypatch.delenv("VAULT_GIT_USER_EMAIL", raising=False)
    vault._git("config", "--unset-all", "user.name", check=False)
    vault._git("config", "--unset-all", "user.email", check=False)

    vault.ensure_git_repo()

    assert vault._git("config", "--get", "user.name").stdout.strip() == "Psycho Bot"
    assert vault._git("config", "--get", "user.email").stdout.strip() == "psycho-bot@local"


def test_ensure_git_repo_applies_author_env_override(as_user, monkeypatch):
    if not vault._git_available():
        pytest.skip("git недоступен")

    monkeypatch.setenv("VAULT_GIT_USER_NAME", "Vault Writer")
    monkeypatch.setenv("VAULT_GIT_USER_EMAIL", "vault-writer@example.test")

    try:
        vault.ensure_git_repo()

        assert vault._git("config", "--get", "user.name").stdout.strip() == "Vault Writer"
        assert (
            vault._git("config", "--get", "user.email").stdout.strip()
            == "vault-writer@example.test"
        )
    finally:
        vault._git("config", "user.name", "Psycho Bot", check=False)
        vault._git("config", "user.email", "psycho-bot@local", check=False)


def test_git_env_uses_configured_ssh_key_without_reading_it(monkeypatch):
    monkeypatch.setenv("VAULT_GIT_SSH_KEY", "/run/secrets/vault git key")

    env = git_storage._git_env()

    assert env is not None
    assert (
        env["GIT_SSH_COMMAND"]
        == "ssh -i '/run/secrets/vault git key' -o IdentitiesOnly=yes "
        "-o StrictHostKeyChecking=accept-new"
    )


def test_commit_all_commits_only_current_user_scope(as_user):
    if not vault._git_available():
        pytest.skip("git недоступен")

    uid1 = as_user
    uid2 = uid1 + 10_000

    note1 = userctx.user_root() / "00_raw" / "notes" / "one.md"
    note1.parent.mkdir(parents=True, exist_ok=True)
    note1.write_text("user one\n", encoding="utf-8")
    userctx.set_user(uid2)
    vault.ensure_layout()
    note2 = userctx.user_root() / "00_raw" / "notes" / "two.md"
    note2.parent.mkdir(parents=True, exist_ok=True)
    note2.write_text("user two\n", encoding="utf-8")

    userctx.set_user(uid1)
    sha = vault.commit_all("scope isolation")
    assert sha

    changed = [
        line.strip()
        for line in vault._git("show", "--name-only", "--format=", sha).stdout.splitlines()
        if line.strip()
    ]
    assert changed
    assert all(line.startswith(f"users/{uid1}/") for line in changed)
    assert not any(line.startswith(f"users/{uid2}/") for line in changed)

    other_status = vault._git(
        "status", "--short", "--untracked-files=all", "--", f"users/{uid2}"
    ).stdout
    assert "two.md" in other_status


def test_commit_all_pushes_only_current_user_scope(as_user, tmp_path):
    if not vault._git_available():
        pytest.skip("git недоступен")

    uid1 = as_user
    uid2 = uid1 + 10_000
    remote = tmp_path / "vault-remote.git"
    subprocess.run(
        ["git", "init", "--bare", str(remote)],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    vault._git("remote", "remove", "origin", check=False)
    vault._git("remote", "add", "origin", str(remote))
    try:
        note1 = userctx.user_root() / "00_raw" / "notes" / "push-one.md"
        note1.parent.mkdir(parents=True, exist_ok=True)
        note1.write_text("user one pushed\n", encoding="utf-8")

        userctx.set_user(uid2)
        vault.ensure_layout()
        note2 = userctx.user_root() / "00_raw" / "notes" / "push-two.md"
        note2.parent.mkdir(parents=True, exist_ok=True)
        note2.write_text("user two must stay local\n", encoding="utf-8")

        userctx.set_user(uid1)
        sha = vault.commit_all("scope push isolation")
        assert sha

        remote_head = subprocess.run(
            ["git", "--git-dir", str(remote), "rev-parse", "refs/heads/main"],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        ).stdout.strip()
        assert remote_head == sha

        changed = [
            line.strip()
            for line in vault._git("show", "--name-only", "--format=", sha).stdout.splitlines()
            if line.strip()
        ]
        assert changed
        assert all(line.startswith(f"users/{uid1}/") for line in changed)
        assert not any(line.startswith(f"users/{uid2}/") for line in changed)

        other_status = vault._git(
            "status", "--short", "--untracked-files=all", "--", f"users/{uid2}"
        ).stdout
        assert "push-two.md" in other_status
    finally:
        vault._git("remote", "remove", "origin", check=False)


def test_git_wrap_rolls_back_on_error(as_user):
    if not vault._git_available():
        pytest.skip("git недоступен")
    with pytest.raises(RuntimeError):
        with vault.git_wrap("unit_rollback"):
            graph.save_concept(_concept("rollme"))
            raise RuntimeError("boom")
    # untracked-файл, созданный в провалившейся транзакции, должен быть вычищен
    assert not graph._path_for("rollme", "ethics").exists()


def test_append_raw_wraps_oserror_in_vaulterror(as_user):
    # Подсовываем директорию вместо файла дня → path.open("a") даёт
    # IsADirectoryError (OSError) → код заворачивает её в доменный VaultError.
    when = datetime.now()
    vault.raw_dir().mkdir(parents=True, exist_ok=True)
    (vault.raw_dir() / f"{when.strftime('%Y-%m-%d')}.md").mkdir()
    with pytest.raises(VaultError):
        vault.append_raw(1, when, "ethics", "вопрос", "ответ")


def test_append_profile_unknown_domain_raises_validationerror(as_user):
    with pytest.raises(ValidationError):
        vault.append_profile(datetime.now(), "not-a-domain", "фрагмент", "12:00")


def test_log_rotation_truncates(as_user, monkeypatch):
    # Понижаем порог и заваливаем журнал: ротация держит размер ограниченным,
    # но свежая запись остаётся на месте.
    monkeypatch.setattr(vault, "_LOG_MAX_BYTES", 2_000)
    for _ in range(400):
        vault.append_log("info", "spam", "x" * 50)
    vault.append_log("warn", "last_marker", "конец")
    size = LOG_PATH.stat().st_size
    assert size < 10_000  # не вырос до ~24 KB всех записей
    assert "last_marker" in LOG_PATH.read_text(encoding="utf-8")
