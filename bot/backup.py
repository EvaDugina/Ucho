"""Снимки файлового хранилища в отдельном каталоге хоста."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .atomic import atomic_write_json

_SNAPSHOT_NAME = re.compile(r"^backup-\d{8}T\d{12}Z$")


@dataclass(frozen=True)
class BackupResult:
    path: Path
    files: int
    bytes: int


def _snapshots(destination: Path) -> list[Path]:
    return sorted(
        path
        for path in destination.iterdir()
        if path.is_dir()
        and not path.is_symlink()
        and _SNAPSHOT_NAME.fullmatch(path.name)
        and (path / "manifest.json").is_file()
    )


def has_backup_this_week(
    destination: Path,
    *,
    now: datetime | None = None,
    tz_name: str = "Europe/Moscow",
) -> bool:
    """Есть ли проверенный снимок за текущую календарную неделю зоны расписания."""
    if not destination.is_dir():
        return False
    zone = ZoneInfo(tz_name)
    week = (now or datetime.now(timezone.utc)).astimezone(zone).isocalendar()[:2]
    for path in _snapshots(destination):
        stamp = datetime.strptime(path.name, "backup-%Y%m%dT%H%M%S%fZ").replace(
            tzinfo=timezone.utc
        )
        if stamp.astimezone(zone).isocalendar()[:2] == week and verify_backup(path):
            return True
    return False


def prune_backups(destination: Path, *, keep: int = 4) -> int:
    """Удалить самые старые готовые снимки, сохраняя максимум четыре."""
    if not 1 <= keep <= 4:
        raise ValueError("BACKUP_KEEP must be between 1 and 4")
    if not destination.is_dir():
        return 0
    root = destination.resolve(strict=True)
    old_paths = _snapshots(root)[:-keep]
    for old in old_paths:
        if old.resolve(strict=False).parent != root:
            raise ValueError("unsafe backup retention path")
        shutil.rmtree(old)
    return len(old_paths)


def create_backup(
    source: Path,
    destination: Path,
    *,
    keep: int = 4,
    now: datetime | None = None,
) -> BackupResult:
    """Скопировать данные, исключив Git, локальные настройки редактора и секреты.

    Вызывается синхронно из event loop: пока идёт копирование, этот процесс бота
    не может менять файлы. Внешний редактор должен сохранять файлы атомарно.
    """
    if not 1 <= keep <= 4:
        raise ValueError("BACKUP_KEEP must be between 1 and 4")
    if source.is_symlink():
        raise ValueError("backup source must not be a symlink")
    source = source.resolve(strict=True)
    destination = destination.resolve(strict=False)
    if not source.is_dir() or source.is_symlink():
        raise ValueError("backup source must be a regular directory")
    if source == destination or source in destination.parents or destination in source.parents:
        raise ValueError("backup directory must be separate from data directory")
    destination.mkdir(parents=True, exist_ok=True)
    stamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).strftime(
        "%Y%m%dT%H%M%S%fZ"
    )
    final = destination / f"backup-{stamp}"
    staging = destination / f".backup-{stamp}.tmp"
    staging.mkdir()
    data = staging / "data"
    data.mkdir()
    manifest: dict[str, object] = {"created_at": stamp, "files": {}}
    count = 0
    total = 0
    try:
        for root, dirs, files in os.walk(source, followlinks=False):
            current = Path(root)
            excluded_dirs = {".git", ".obsidian", ".ssh"}
            for name in dirs:
                if name not in excluded_dirs and (current / name).is_symlink():
                    raise ValueError(f"symlink in backup source: {current / name}")
            dirs[:] = sorted(name for name in dirs if name not in excluded_dirs)
            relative_dir = current.relative_to(source)
            (data / relative_dir).mkdir(parents=True, exist_ok=True)
            for name in sorted(files):
                if (
                    name.endswith(".tmp")
                    or name == ".env"
                    or name.startswith(".env.")
                    or name.endswith((".pem", ".key", ".ppk"))
                ):
                    continue
                original = current / name
                if original.is_symlink() or not original.is_file():
                    raise ValueError(f"unsafe backup source file: {original}")
                relative = original.relative_to(source)
                target = data / relative
                shutil.copy2(original, target)
                with target.open("rb+") as handle:
                    digest = hashlib.file_digest(handle, "sha256").hexdigest()
                    os.fsync(handle.fileno())
                size = target.stat().st_size
                manifest["files"][relative.as_posix()] = {"sha256": digest, "bytes": size}
                count += 1
                total += size
        manifest["file_count"] = count
        manifest["total_bytes"] = total
        atomic_write_json(staging / "manifest.json", manifest)
        os.replace(staging, final)
    except Exception:
        if staging.resolve(strict=False).parent == destination:
            shutil.rmtree(staging, ignore_errors=True)
        raise

    prune_backups(destination, keep=keep)
    return BackupResult(final, count, total)


def verify_backup(path: Path) -> bool:
    """Проверить состав и хеши готового снимка перед восстановлением."""
    try:
        manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
        files = manifest["files"]
        if not isinstance(files, dict):
            return False
        data = path / "data"
        actual = {item.relative_to(data).as_posix() for item in data.rglob("*") if item.is_file()}
        if actual != set(files):
            return False
        for name, expected in files.items():
            target = data / name
            if target.is_symlink() or not target.resolve(strict=False).is_relative_to(
                data.resolve(strict=False)
            ):
                return False
            with target.open("rb") as handle:
                digest = hashlib.file_digest(handle, "sha256").hexdigest()
            if digest != expected["sha256"] or target.stat().st_size != expected["bytes"]:
                return False
        return True
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return False


def restore_backup(snapshot: Path, destination: Path) -> Path:
    """Восстановить проверенный снимок только в новый каталог."""
    snapshot = snapshot.resolve(strict=True)
    destination = destination.resolve(strict=False)
    if not verify_backup(snapshot):
        raise ValueError("backup verification failed")
    if destination.exists() or snapshot == destination:
        raise ValueError("restore destination must not exist")
    if snapshot in destination.parents or destination in snapshot.parents:
        raise ValueError("restore destination must be separate from backup")
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = destination.parent / f".{destination.name}-restore-{uuid.uuid4().hex}"
    try:
        shutil.copytree(snapshot / "data", staging, symlinks=False)
        os.rename(staging, destination)
    except Exception:
        if staging.exists() and staging.resolve(strict=False).parent == destination.parent:
            shutil.rmtree(staging)
        raise
    return destination


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Создать, проверить или восстановить снимок")
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create")
    create.add_argument("source", type=Path)
    create.add_argument("destination", type=Path)
    create.add_argument("--keep", type=int, default=4)
    verify = commands.add_parser("verify")
    verify.add_argument("snapshot", type=Path)
    restore = commands.add_parser("restore")
    restore.add_argument("snapshot", type=Path)
    restore.add_argument("destination", type=Path)
    args = parser.parse_args(argv)
    if args.command == "create":
        result = create_backup(args.source, args.destination, keep=args.keep)
        print(f"{result.path} files={result.files} bytes={result.bytes}")
        return 0
    if args.command == "verify":
        ok = verify_backup(args.snapshot)
        print("OK" if ok else "FAILED")
        return 0 if ok else 1
    target = restore_backup(args.snapshot, args.destination)
    print(target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
