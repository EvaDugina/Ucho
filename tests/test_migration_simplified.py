from __future__ import annotations

import json
import subprocess

from scripts import migrate_simplified_storage as migration


def _legacy_fixture(vault, uid="42"):
    root = vault / "users" / uid
    qna = root / "00_raw" / "qna"
    qna.mkdir(parents=True)
    (qna / "2026-07-20.md").write_text(
        "# 2026-07-20\n\n"
        "## Q7 · 12:30 · ethics\n"
        "**Q:** Что важнее?\n"
        "**A:** Честность важнее выгоды.\n"
        "^Q7\n\n",
        encoding="utf-8",
    )
    personality = root / "03_personality"
    personality.mkdir()
    (personality / "about.md").write_text("# Старый профиль\n", encoding="utf-8")
    (personality / "mood.md").write_text("---\nvalence: -0.2\n---\n", encoding="utf-8")
    (personality / "deltas.jsonl").write_text(
        json.dumps({"user_delta": {"Ценности": "Выбирает честность."}}, ensure_ascii=False)
        + "\n",
        encoding="utf-8",
    )
    (root / "02_concepts").mkdir()
    (root / "02_concepts" / "old.md").write_text("legacy", encoding="utf-8")
    return root


def test_preview_apply_and_idempotency(tmp_path, capsys):
    root = _legacy_fixture(tmp_path)
    assert migration.main(["--vault", str(tmp_path)]) == 0
    assert (root / "00_raw" / "qna").exists()
    assert not (root / "00_raw" / "sessions" / "legacy-import.jsonl").exists()

    assert migration.main(["--vault", str(tmp_path), "--apply"]) == 0
    log = root / "00_raw" / "sessions" / "legacy-import.jsonl"
    rows = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert [row["kind"] for row in rows] == ["legacy_question", "legacy_answer"]
    assert not (root / "00_raw" / "qna").exists()
    assert not (root / "02_concepts").exists()
    assert not (root / "03_personality").exists()
    assert (root / "01_mood" / "current.md").exists()
    assert (root / "01_personality" / "about" / "current.md").exists()
    deltas = json.loads(
        (root / "01_personality" / "deltas.json").read_text(encoding="utf-8")
    )
    assert deltas["items"][0]["status"] == "pending"

    before = log.read_text(encoding="utf-8")
    assert migration.main(["--vault", str(tmp_path), "--apply"]) == 0
    assert log.read_text(encoding="utf-8") == before
    capsys.readouterr()


def test_user_transaction_rolls_back(tmp_path, monkeypatch):
    root = _legacy_fixture(tmp_path)
    subprocess.run(["git", "init", "-b", "main"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "fixture"], cwd=tmp_path, check=True)

    def fail(_):
        raise RuntimeError("boom")

    monkeypatch.setattr(migration, "_migrate_derived", fail)
    assert migration.main(["--vault", str(tmp_path), "--apply"]) == 1
    assert (root / "00_raw" / "qna" / "2026-07-20.md").exists()
    assert not (root / "00_raw" / "sessions" / "legacy-import.jsonl").exists()


def test_unparsed_legacy_markdown_is_kept(tmp_path):
    root = tmp_path / "users" / "42"
    notes = root / "00_raw" / "notes"
    notes.mkdir(parents=True)
    source = notes / "manual.md"
    source.write_text("# Ручная запись\n\nФормат был изменён человеком.\n", encoding="utf-8")
    assert migration.main(["--vault", str(tmp_path), "--apply"]) == 1
    assert source.exists()
