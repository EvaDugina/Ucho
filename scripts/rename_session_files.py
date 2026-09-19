"""Переименовать raw/Markdown-сессии в timestamp_uuid без правки событий.

Без аргументов показывает preview. --apply запускать при остановленном боте
после проверяемого снимка vault.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path

from bot import session_log
from bot.config import VAULT_PATH


@dataclass(frozen=True)
class Rename:
    raw: Path
    target: Path


def plan(root: Path, *, uid: int | None = None) -> tuple[list[Rename], int]:
    changes: list[Rename] = []
    already_named = 0
    targets: set[Path] = set()
    directories = (
        [(root / "users" / str(uid) / "00_raw" / "sessions")]
        if uid is not None else sorted((root / "users").glob("*/00_raw/sessions"))
    )
    for directory in directories:
        if directory.is_symlink() or not directory.is_dir():
            raise ValueError("unsafe sessions directory")
        for raw in sorted(directory.glob("*.jsonl")):
            if raw.is_symlink():
                raise ValueError("raw session is a symlink")
            rows = [
                json.loads(line)
                for line in raw.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            if not rows or any(not isinstance(row, dict) for row in rows):
                raise ValueError("empty or malformed raw session")
            session_id = rows[0].get("session_id")
            if (
                not isinstance(session_id, str)
                or not session_log.SESSION_ID_RE.fullmatch(session_id)
                or any(row.get("session_id") != session_id for row in rows)
            ):
                raise ValueError("raw session IDs disagree")
            if not rows[0].get("ts"):
                raise ValueError("first raw event has no timestamp")
            target = raw.with_name(
                session_log.timestamped_filename(session_id, rows[0]["ts"])
            )
            if session_log.SESSION_FILE_RE.fullmatch(raw.stem):
                if raw.name != target.name:
                    raise ValueError("timestamped filename disagrees with first event")
                already_named += 1
                continue
            view = raw.with_suffix(".md")
            target_view = target.with_suffix(".md")
            if not view.is_file() or view.is_symlink():
                raise ValueError("Markdown view missing or unsafe")
            if (
                target.exists()
                or target.is_symlink()
                or target_view.exists()
                or target_view.is_symlink()
                or target in targets
            ):
                raise ValueError("session filename collision")
            targets.add(target)
            changes.append(Rename(raw, target))
    return changes, already_named


def apply(changes: list[Rename]) -> None:
    for item in changes:
        old_view = item.raw.with_suffix(".md")
        new_view = item.target.with_suffix(".md")
        item.raw.rename(item.target)
        try:
            old_view.rename(new_view)
        except Exception:
            item.target.rename(item.raw)
            raise
        session_log._write_markdown_view(item.target)


def main() -> int:
    parser = argparse.ArgumentParser(description="Переименовать файлы raw-сессий")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    changes, already_named = plan(VAULT_PATH)
    print(f"to_rename={len(changes)} already_named={already_named}")
    if args.apply:
        apply(changes)
        print(f"renamed={len(changes)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
