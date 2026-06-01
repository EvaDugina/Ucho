"""Tests for deploy shell helpers without touching real server secrets."""

from __future__ import annotations

import shlex
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "deploy" / "lib.sh"


def _write_env(app_dir: Path, body: str) -> None:
    app_dir.mkdir(parents=True, exist_ok=True)
    (app_dir / ".env").write_text(body, encoding="utf-8")


def _run_lib(app_dir: Path, script: str) -> subprocess.CompletedProcess[str]:
    quoted_app = shlex.quote(str(app_dir))
    quoted_vault = shlex.quote(str(app_dir / "vault"))
    quoted_lib = shlex.quote(str(LIB))
    return subprocess.run(
        [
            "bash",
            "-lc",
            (
                "set -Eeuo pipefail; "
                f"APP_DIR={quoted_app}; "
                f"VAULT_DIR={quoted_vault}; "
                f". {quoted_lib}; "
                f"{script}"
            ),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def _minimal_env(*, llm_key: str = "AITUNNEL_API_KEY=test-llm-key") -> str:
    return "\n".join(
        [
            "TELEGRAM_BOT_TOKEN=test-telegram-token",
            "OWNER_TELEGRAM_ID=123",
            "VAULT_HOST_PATH=/tmp/psycho-vault",
            llm_key,
            "",
        ]
    )


def test_preflight_empty_env_lists_required_names_without_values(tmp_path: Path) -> None:
    app_dir = tmp_path / "app"
    _write_env(app_dir, "")

    result = _run_lib(app_dir, "preflight_env")

    assert result.returncode != 0
    assert "TELEGRAM_BOT_TOKEN" in result.stderr
    assert "OWNER_TELEGRAM_ID" in result.stderr
    assert "VAULT_HOST_PATH" in result.stderr
    assert "OPENROUTER_API_KEY or AITUNNEL_API_KEY" in result.stderr
    assert "test-telegram-token" not in result.stderr
    assert "test-llm-key" not in result.stderr


def test_preflight_accepts_aitunnel_key(tmp_path: Path) -> None:
    app_dir = tmp_path / "app"
    _write_env(app_dir, _minimal_env())

    result = _run_lib(app_dir, "preflight_env")

    assert result.returncode == 0, result.stderr


def test_preflight_accepts_openrouter_instead_of_aitunnel(tmp_path: Path) -> None:
    app_dir = tmp_path / "app"
    _write_env(app_dir, _minimal_env(llm_key="OPENROUTER_API_KEY=test-openrouter-key"))

    result = _run_lib(app_dir, "preflight_env")

    assert result.returncode == 0, result.stderr


def test_preflight_rejects_missing_host_ssh_key_before_compose(tmp_path: Path) -> None:
    app_dir = tmp_path / "app"
    missing_key = tmp_path / "missing_deploy_key"
    _write_env(
        app_dir,
        _minimal_env() + f"VAULT_GIT_SSH_KEY_HOST_PATH={missing_key}\n",
    )

    result = _run_lib(app_dir, "preflight_env")

    assert result.returncode != 0
    assert "VAULT_GIT_SSH_KEY_HOST_PATH" in result.stderr
    assert str(missing_key) in result.stderr


def test_vault_git_uses_git_ssh_command_without_reading_key(tmp_path: Path) -> None:
    app_dir = tmp_path / "app"
    vault_dir = tmp_path / "vault"
    fake_bin = tmp_path / "bin"
    fake_git = fake_bin / "git"
    key_path = tmp_path / "deploy key"
    secret_marker = "PRIVATE-KEY-CONTENT-SHOULD-NOT-APPEAR"
    fake_bin.mkdir()
    vault_dir.mkdir()
    key_path.write_text(secret_marker, encoding="utf-8")
    fake_git.write_text(
        "#!/usr/bin/env bash\n"
        "printf '%s\\n' \"$GIT_SSH_COMMAND\"\n"
        "printf '%s\\n' \"$@\" >&2\n",
        encoding="utf-8",
    )
    fake_git.chmod(0o755)
    _write_env(
        app_dir,
        _minimal_env() + f"VAULT_GIT_SSH_KEY_HOST_PATH={key_path}\n",
    )

    script = (
        f"PATH={shlex.quote(str(fake_bin))}:$PATH; "
        f"VAULT_DIR={shlex.quote(str(vault_dir))}; "
        "vault_git status"
    )
    result = _run_lib(app_dir, script)

    assert result.returncode == 0, result.stderr
    assert "ssh -i" in result.stdout
    assert str(key_path).replace(" ", "\\ ") in result.stdout
    assert "IdentitiesOnly=yes" in result.stdout
    assert "StrictHostKeyChecking=accept-new" in result.stdout
    assert secret_marker not in result.stdout
    assert secret_marker not in result.stderr
