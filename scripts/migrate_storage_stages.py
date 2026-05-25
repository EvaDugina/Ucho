"""Миграция vault Psycho на stage-структуру 00-03.

Запускать в Docker:

    python scripts/migrate_storage_stages.py --vault /vault --dry-run
    python scripts/migrate_storage_stages.py --vault /vault

Скрипт намеренно stdlib-only: он работает по файловой структуре vault и не
импортирует bot.config, чтобы не читать `.env`.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


LINK_REPLACEMENTS = (
    ("[[raw/", "[[00_raw/qna/"),
    ("[[concepts/", "[[02_concepts/"),
    ("[[profile/", "[[02_profile/"),
    ("[[digests/", "[[02_digest/"),
    ("[[personality/", "[[03_personality/"),
    ("[[mood/", "[[01_mood/"),
)

TEXT_SUFFIXES = {".md", ".json", ".jsonl"}
RUNTIME_DELETE = ("_qmap.json", "_questions.json", "_sessions.json")
TEXT_FIELDS = {"assistant_text", "user_text", "question", "session_context"}


@dataclass
class Stats:
    moved: dict[str, int] = field(default_factory=dict)
    deleted: dict[str, int] = field(default_factory=dict)
    rewritten_links: int = 0
    migrated_inbox_unique: int = 0
    stripped_text_fields: int = 0
    compacted_session: int = 0
    conflicts: list[str] = field(default_factory=list)

    def bump(self, bucket: str, key: str, n: int = 1) -> None:
        target = self.moved if bucket == "moved" else self.deleted
        target[key] = target.get(key, 0) + n


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _write_json(path: Path, data: Any, dry_run: bool) -> None:
    if dry_run:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _append_jsonl(path: Path, rows: list[dict], dry_run: bool) -> None:
    if not rows or dry_run:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def _merge_file(src: Path, dst: Path, stats: Stats, dry_run: bool, label: str) -> None:
    if not src.exists():
        return
    if dst.exists():
        if src.read_bytes() == dst.read_bytes():
            if not dry_run:
                src.unlink()
            stats.bump("deleted", "duplicate_file")
            return
        stats.conflicts.append(f"{src} -> {dst}")
        return
    stats.bump("moved", label)
    if dry_run:
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dst))


def _merge_dir(src: Path, dst: Path, stats: Stats, dry_run: bool, label: str) -> None:
    if not src.exists():
        return
    if not src.is_dir():
        _merge_file(src, dst, stats, dry_run, label)
        return
    for child in sorted(src.iterdir()):
        target = dst / child.name
        if child.is_dir():
            _merge_dir(child, target, stats, dry_run, label)
        else:
            _merge_file(child, target, stats, dry_run, label)
    if not dry_run:
        try:
            src.rmdir()
        except OSError:
            pass


def _session_log_dir(user: Path) -> Path:
    return user / "00_raw" / "sessions"


def _iter_session_events(user: Path) -> list[dict]:
    out: list[dict] = []
    for path in sorted(_session_log_dir(user).glob("*.jsonl")):
        for idx, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                row.setdefault("session_id", path.stem)
                row.setdefault("event_id", f"{path.stem}:{idx:06d}")
                out.append(row)
    return out


def _line_count(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())


def _append_session_event(user: Path, session_id: str, row: dict, dry_run: bool) -> dict:
    path = _session_log_dir(user) / f"{session_id}.jsonl"
    event = {
        "event_id": f"{session_id}:{_line_count(path) + 1:06d}",
        "ts": row.get("ts") or datetime.now().isoformat(timespec="seconds"),
        "session_id": session_id,
        "role": row.get("role") or "user",
        "kind": row.get("kind") or "answer",
        "telegram_message_id": row.get("telegram_message_id", row.get("message_id")),
        "message_id": row.get("telegram_message_id", row.get("message_id")),
        "reply_to_message_id": row.get("reply_to_message_id"),
        "q_num": row.get("q_num"),
        "domain": row.get("domain"),
        "bot_mood": row.get("bot_mood"),
        "text": row.get("text") or "",
        "source": row.get("source") or "legacy_migration",
    }
    if not dry_run:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, ensure_ascii=False) + "\n")
    return event


def _qna_texts(user: Path) -> set[str]:
    qna = user / "00_raw" / "qna"
    texts: set[str] = set()
    for path in list(qna.glob("*.md")) + list((user / "raw").glob("*.md")):
        texts.add(path.read_text(encoding="utf-8"))
    return texts


def migrate_inbox(user: Path, stats: Stats, dry_run: bool) -> None:
    inbox = user / "raw" / "inbox"
    if not inbox.exists():
        return
    existing_events = _iter_session_events(user)
    existing_mids = {
        int(e.get("telegram_message_id", e.get("message_id")))
        for e in existing_events
        if e.get("telegram_message_id", e.get("message_id")) is not None
    }
    qna_text_blobs = _qna_texts(user)
    for path in sorted(inbox.glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(row, dict):
                continue
            text = (row.get("text") or "").strip()
            mid = row.get("message_id")
            if not text:
                continue
            if isinstance(mid, int) and mid in existing_mids:
                continue
            if any(text in blob for blob in qna_text_blobs):
                continue
            sid = row.get("session_id") or f"legacy-inbox-{path.stem}"
            kind = row.get("kind") or "text"
            if kind == "text":
                kind = "answer" if row.get("q_num") else "note_open"
            _append_session_event(
                user,
                str(sid),
                {
                    **row,
                    "role": "user",
                    "kind": kind,
                    "source": "legacy_inbox",
                },
                dry_run,
            )
            stats.migrated_inbox_unique += 1
    stats.bump("deleted", "raw/inbox")
    if not dry_run:
        shutil.rmtree(inbox, ignore_errors=True)


def move_legacy_tree(user: Path, stats: Stats, dry_run: bool) -> None:
    _merge_dir(user / "raw" / "sessions", user / "00_raw" / "sessions", stats, dry_run, "raw_sessions")
    qna_dst = user / "00_raw" / "qna"
    raw = user / "raw"
    if raw.exists():
        for md in sorted(raw.glob("*.md")):
            _merge_file(md, qna_dst / md.name, stats, dry_run, "raw_qna")
    migrate_inbox(user, stats, dry_run)
    _merge_dir(user / "notes", user / "00_raw" / "notes", stats, dry_run, "notes")
    _merge_dir(user / "concepts", user / "02_concepts", stats, dry_run, "concepts")
    _merge_dir(user / "profile", user / "02_profile", stats, dry_run, "profile")
    _merge_dir(user / "digests", user / "02_digest", stats, dry_run, "digests")
    _merge_dir(user / "personality", user / "03_personality", stats, dry_run, "personality")
    _merge_file(user / "user_prompt.md", user / "03_personality" / "user_prompt.md", stats, dry_run, "user_prompt")
    _merge_dir(user / "mood" / "analysis", user / "01_mood" / "analysis", stats, dry_run, "mood_analysis")
    _merge_dir(user / "mood" / "timeseries", user / "01_mood" / "timeseries", stats, dry_run, "mood_timeseries")
    _merge_file(user / "mood" / "График настроения.md", user / "01_mood" / "График настроения.md", stats, dry_run, "mood_chart")
    _merge_file(user / "mood" / "_mood_map.json", user / "01_mood" / "_mood_map.json", stats, dry_run, "mood_map")
    if not dry_run:
        for legacy_dir in (user / "raw", user / "mood"):
            try:
                legacy_dir.rmdir()
            except OSError:
                pass


def migrate_mood_log(user: Path, stats: Stats, dry_run: bool) -> None:
    src = user / "_mood_log.jsonl"
    if not src.exists():
        return
    buckets: dict[str, list[dict]] = {}
    for line in src.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        ts = str(row.get("ts") or "")
        month = ts[:7] if len(ts) >= 7 else datetime.now().strftime("%Y-%m")
        row.setdefault("source", "legacy_mood_log")
        buckets.setdefault(month, []).append(row)
    for month, rows in buckets.items():
        _append_jsonl(user / "01_mood" / "events" / f"{month}.jsonl", rows, dry_run)
    stats.bump("moved", "_mood_log")
    if not dry_run:
        src.unlink()


def _strip_text_fields(obj: Any) -> tuple[Any, int]:
    count = 0
    if isinstance(obj, dict):
        new = {}
        for key, value in obj.items():
            if key in TEXT_FIELDS:
                count += 1
                continue
            new_value, sub = _strip_text_fields(value)
            count += sub
            new[key] = new_value
        return new, count
    if isinstance(obj, list):
        out = []
        for value in obj:
            new_value, sub = _strip_text_fields(value)
            count += sub
            out.append(new_value)
        return out, count
    return obj, 0


def migrate_runtime_json(user: Path, stats: Stats, dry_run: bool) -> None:
    _merge_file(user / "_user_deltas.jsonl", user / "03_personality" / "deltas.jsonl", stats, dry_run, "user_deltas")
    _merge_file(user / "_mood_feedback.jsonl", user / "01_mood" / "feedback.jsonl", stats, dry_run, "mood_feedback")

    for src_name, dst in (
        ("_face_actions.json", user / "03_personality" / "face_actions.json"),
        ("_liked_replies.json", user / "03_personality" / "liked_replies.json"),
    ):
        src = user / src_name
        if not src.exists():
            continue
        data = _read_json(src, {})
        data, stripped = _strip_text_fields(data)
        stats.stripped_text_fields += stripped
        _write_json(dst, data, dry_run)
        stats.bump("moved", src_name)
        if not dry_run:
            src.unlink()

    liked_log = user / "_liked_replies_log.jsonl"
    if liked_log.exists():
        rows = []
        for line in liked_log.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            row, stripped = _strip_text_fields(row)
            stats.stripped_text_fields += stripped
            rows.append(row)
        _append_jsonl(user / "03_personality" / "liked_replies_log.jsonl", rows, dry_run)
        stats.bump("moved", "_liked_replies_log")
        if not dry_run:
            liked_log.unlink()

    for name in RUNTIME_DELETE:
        path = user / name
        if path.exists():
            stats.bump("deleted", name)
            if not dry_run:
                path.unlink()


def compact_session_json(user: Path, stats: Stats, dry_run: bool) -> None:
    path = user / "_session.json"
    if not path.exists():
        return
    data = _read_json(path, {})
    if not isinstance(data, dict):
        return
    original = json.dumps(data, ensure_ascii=False, sort_keys=True)
    sid = data.get("id")
    history = data.get("history") if isinstance(data.get("history"), list) else []
    for item in history:
        if not isinstance(item, dict) or not sid:
            continue
        text = item.get("content") or item.get("text") or ""
        if not text:
            continue
        _append_session_event(
            user,
            str(sid),
            {
                "ts": item.get("ts") or item.get("timestamp"),
                "role": item.get("role"),
                "kind": "legacy_history",
                "text": text,
                "source": "legacy_session_history",
            },
            dry_run,
        )
    pending = (data.get("pending_answer") or "").strip()
    if pending and not data.get("pending_answer_event_id") and sid:
        event = _append_session_event(
            user,
            str(sid),
            {
                "role": "user",
                "kind": "answer",
                "text": pending,
                "q_num": data.get("current_q_num"),
                "domain": data.get("last_domain") or data.get("domain"),
                "source": "legacy_session_pending",
            },
            dry_run,
        )
        data["pending_answer_event_id"] = event["event_id"]
        data["pending_answer"] = None
    data["history"] = []
    if json.dumps(data, ensure_ascii=False, sort_keys=True) != original:
        stats.compacted_session += 1
        _write_json(path, data, dry_run)


def rewrite_links(user: Path, stats: Stats, dry_run: bool) -> None:
    for path in user.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        if ".obsidian" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        new_text = text
        for old, new in LINK_REPLACEMENTS:
            new_text = new_text.replace(old, new)
        if new_text != text:
            stats.rewritten_links += 1
            if not dry_run:
                path.write_text(new_text, encoding="utf-8")


def migrate_user(user: Path, dry_run: bool) -> Stats:
    stats = Stats()
    if not dry_run:
        for rel in (
            "00_raw/sessions",
            "00_raw/qna",
            "00_raw/notes",
            "01_mood/events",
            "01_mood/analysis",
            "01_mood/timeseries",
            "02_concepts",
            "02_profile",
            "02_digest",
            "03_personality",
        ):
            (user / rel).mkdir(parents=True, exist_ok=True)
    move_legacy_tree(user, stats, dry_run)
    migrate_mood_log(user, stats, dry_run)
    migrate_runtime_json(user, stats, dry_run)
    compact_session_json(user, stats, dry_run)
    rewrite_links(user, stats, dry_run)
    return stats


def users_root(vault: Path) -> list[Path]:
    root = vault / "users"
    if root.exists():
        return [p for p in sorted(root.iterdir()) if p.is_dir()]
    return [vault]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--vault", default=os.environ.get("VAULT_PATH") or "/vault")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    vault = Path(args.vault)
    all_stats: dict[str, Stats] = {}
    for user in users_root(vault):
        all_stats[user.name] = migrate_user(user, args.dry_run)

    print(json.dumps({
        uid: {
            "moved": st.moved,
            "deleted": st.deleted,
            "rewritten_links": st.rewritten_links,
            "migrated_inbox_unique": st.migrated_inbox_unique,
            "stripped_text_fields": st.stripped_text_fields,
            "compacted_session": st.compacted_session,
            "conflicts": st.conflicts,
        }
        for uid, st in all_stats.items()
    }, ensure_ascii=False, indent=2))
    return 1 if any(st.conflicts for st in all_stats.values()) else 0


if __name__ == "__main__":
    raise SystemExit(main())
