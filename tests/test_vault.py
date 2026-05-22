"""Юнит-тесты хранилища: сквозная нумерация, атомарная запись, git-транзакция."""
from __future__ import annotations

import pytest

from bot import graph, vault
from bot.atomic import atomic_write_text
from bot.graph import Concept


def test_next_q_num_monotonic(as_user):
    a = vault.next_q_num()
    b = vault.next_q_num()
    assert b == a + 1


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


def test_git_wrap_rolls_back_on_error(as_user):
    if not vault._git_available():
        pytest.skip("git недоступен")
    with pytest.raises(RuntimeError):
        with vault.git_wrap("unit_rollback"):
            graph.save_concept(_concept("rollme"))
            raise RuntimeError("boom")
    # untracked-файл, созданный в провалившейся транзакции, должен быть вычищен
    assert not graph._path_for("rollme", "ethics").exists()
