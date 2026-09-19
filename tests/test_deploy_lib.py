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
            "VAULT_HOST_PATH=/tmp/ucho-vault",
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
