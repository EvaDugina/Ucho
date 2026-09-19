from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from bot import backup


def test_backup_is_complete_excludes_git_and_rotates(tmp_path):
    source = tmp_path / "data"
    destination = tmp_path / "backups"
    (source / "users" / "42").mkdir(parents=True)
    (source / "users" / "42" / "about.md").write_text("Портрет\n", encoding="utf-8")
    (source / ".git").mkdir()
    (source / ".git" / "HEAD").write_text("old history", encoding="utf-8")
    (source / ".obsidian").mkdir()
    (source / ".obsidian" / "graph.json").write_text("local editor", encoding="utf-8")
    (source / "users" / "42" / "state.json.tmp").write_text("unfinished", encoding="utf-8")

    first = backup.create_backup(
        source, destination, keep=1, now=datetime(2026, 9, 19, tzinfo=timezone.utc)
    )
    assert first.files == 1
    assert backup.verify_backup(first.path)
    restored = backup.restore_backup(first.path, tmp_path / "restored")
    assert (restored / "users" / "42" / "about.md").read_text(encoding="utf-8") == "Портрет\n"
    with pytest.raises(ValueError):
        backup.restore_backup(first.path, restored)
    assert not (first.path / "data" / ".git").exists()
    assert not (first.path / "data" / ".obsidian").exists()
    assert not (first.path / "data" / "users" / "42" / "state.json.tmp").exists()
    assert backup.has_backup_this_week(
        destination, now=datetime(2026, 9, 19, tzinfo=timezone.utc)
    )

    second = backup.create_backup(
        source, destination, keep=1, now=datetime(2026, 9, 20, tzinfo=timezone.utc)
    )
    assert backup.verify_backup(second.path)
    assert not first.path.exists()
    (second.path / "data" / "users" / "42" / "about.md").write_text(
        "Повреждено\n", encoding="utf-8"
    )
    assert not backup.verify_backup(second.path)


def test_backup_rejects_nested_target(tmp_path):
    source = tmp_path / "data"
    source.mkdir()
    with pytest.raises(ValueError):
        backup.create_backup(source, source / "backups")


def test_week_boundary_uses_schedule_timezone(tmp_path):
    source = tmp_path / "data"
    source.mkdir()
    destination = tmp_path / "backups"
    backup.create_backup(
        source, destination, now=datetime(2026, 9, 20, 20, 0, tzinfo=timezone.utc)
    )
    assert backup.has_backup_this_week(
        destination, now=datetime(2026, 9, 20, 20, 30, tzinfo=timezone.utc)
    )
    assert not backup.has_backup_this_week(
        destination, now=datetime(2026, 9, 20, 21, 30, tzinfo=timezone.utc)
    )


def test_default_rotation_keeps_four_copies_of_each_user(tmp_path):
    source = tmp_path / "data"
    for uid in ("42", "77"):
        directory = source / "users" / uid
        directory.mkdir(parents=True)
        (directory / "about.md").write_text(uid, encoding="utf-8")
    destination = tmp_path / "backups"
    first = None
    for week in range(5):
        result = backup.create_backup(
            source,
            destination,
            now=datetime(2026, 9, 21, tzinfo=timezone.utc) + timedelta(weeks=week),
        )
        if first is None:
            first = result.path
    snapshots = backup._snapshots(destination)
    assert len(snapshots) == 4
    assert not first.exists()
    assert all(
        (snapshot / "data" / "users" / uid / "about.md").exists()
        for snapshot in snapshots
        for uid in ("42", "77")
    )


def test_startup_rotation_removes_excess_old_copies(tmp_path):
    source = tmp_path / "data"
    source.mkdir()
    destination = tmp_path / "backups"
    paths = [
        backup.create_backup(
            source,
            destination,
            keep=4,
            now=datetime(2026, 9, 21, tzinfo=timezone.utc) + timedelta(weeks=week),
        ).path
        for week in range(4)
    ]
    assert backup.prune_backups(destination, keep=3) == 1
    assert not paths[0].exists()
    assert len(backup._snapshots(destination)) == 3
