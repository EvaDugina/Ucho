"""Миграция старого vault в raw/mood/personality + books.

Без ``--apply`` команда только печатает preview. Запускать в Docker:
``docker compose run --rm bot python scripts/migrate_simplified_storage.py``.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

QNA_RE = re.compile(
    r"##\s+Q(?P<number>\d+)\s*[·\-—]\s*(?P<time>\d{2}:\d{2})"
    r"\s*[·\-—]\s*(?P<topic>[^\n]+?)\s*\n"
    r"\*\*Q:\*\*\s*(?P<question>.*?)\n"
    r"\*\*A:\*\*\s*(?P<answer>.*?)(?=\n##\s+Q\d+\s*[·\-—]|\Z)",
    re.DOTALL,
)
NOTE_RE = re.compile(
    r"^##\s+(?P<time>\d{2}:\d{2})\s*\n(?P<text>.*?)(?=^##\s+\d{2}:\d{2}\s*$|\Z)",
    re.DOTALL | re.MULTILINE,
)
FACE_FILES = {
    "face_actions.json",
    "feedback.jsonl",
    "liked_replies.json",
    "liked_replies_log.jsonl",
    "mask_frequencies.json",
    "mask_frequencies_draft.json",
    "mask_preferences.json",
}
LEGACY_DIRS = {
    "02_concepts",
    "02_profile",
    "02_digest",
    "03_personality",
    "01_Мироощущение",
    "02_Миропонимание",
    "03_Ценностно-нормативная подсистема",
    "04_Практический уровень",
    "05_Общее",
}
MOOD_JUNK = {
    "analysis",
    "timeseries",
    "reports",
    "charts",
    "График настроения.md",
}
GRAPH_TEMPLATE = {
    "collapse-filter": False,
    "search": (
        "(path:01_Мироощущение/atoms OR path:02_Миропонимание/atoms OR "
        "path:03_Ценностно-нормативная подсистема/atoms OR "
        "path:04_Практический уровень/atoms) -file:MOC"
    ),
    "showTags": True,
    "showAttachments": False,
    "hideUnresolved": True,
    "showOrphans": False,
    "collapse-color-groups": False,
    "colorGroups": [
        {"query": "path:01_Мироощущение/atoms", "color": {"a": 1, "rgb": 15562031}},
        {"query": "path:02_Миропонимание/atoms", "color": {"a": 1, "rgb": 7780274}},
        {
            "query": "path:03_Ценностно-нормативная подсистема/atoms",
            "color": {"a": 1, "rgb": 14767961},
        },
        {"query": "path:04_Практический уровень/atoms", "color": {"a": 1, "rgb": 9925547}},
    ],
    "collapse-display": True,
    "showArrow": True,
    "textFadeMultiplier": -0.5,
    "nodeSizeMultiplier": 1.42708333333333,
    "lineSizeMultiplier": 0.40625,
    "collapse-forces": True,
    "centerStrength": 0.510416666666667,
    "repelStrength": 13.8541666666667,
    "linkStrength": 0.505208333333333,
    "linkDistance": 30,
    "scale": 0.7132754626224402,
    "close": False,
}


def _atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


def _atomic_json(path: Path, value: object) -> None:
    _atomic_text(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _key(kind: str, *parts: str) -> str:
    payload = "\x1f".join([kind, *(part.strip() for part in parts)])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _date_from_path(path: Path) -> str:
    match = re.search(r"\d{4}-\d{2}-\d{2}", path.stem)
    return match.group(0) if match else "1970-01-01"


def _clean_legacy_answer(value: str) -> str:
    return re.sub(r"\n\^Q\d+\s*$", "", value.strip())


def _legacy_events(user_root: Path) -> list[dict]:
    candidates: list[Path] = []
    for directory in (
        user_root / "00_raw" / "qna",
        user_root / "00_raw" / "notes",
        user_root / "notes",
    ):
        if directory.exists():
            candidates.extend(sorted(directory.rglob("*.md")))
    raw_root = user_root / "00_raw"
    if raw_root.exists():
        candidates.extend(sorted(path for path in raw_root.glob("*.md") if path.is_file()))

    events: list[dict] = []
    seen: set[str] = set()
    for path in candidates:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        qna = list(QNA_RE.finditer(text))
        if qna:
            day = _date_from_path(path)
            for match in qna:
                question = match.group("question").strip()
                answer = _clean_legacy_answer(match.group("answer"))
                migration_key = _key("qna", question, answer)
                if migration_key in seen:
                    continue
                seen.add(migration_key)
                common = {
                    "ts": f"{day}T{match.group('time')}:00",
                    "session_id": "legacy-import",
                    "q_num": int(match.group("number")),
                    "domain": match.group("topic").strip(),
                    "metadata": {
                        "source": "legacy_qna",
                        "migration_key": migration_key,
                        "source_path": str(path.relative_to(user_root)).replace("\\", "/"),
                    },
                }
                events.append(
                    {
                        "event_id": f"legacy-{migration_key[:24]}-q",
                        "role": "assistant",
                        "kind": "legacy_question",
                        "text": question,
                        **common,
                    }
                )
                events.append(
                    {
                        "event_id": f"legacy-{migration_key[:24]}-a",
                        "role": "user",
                        "kind": "legacy_answer",
                        "text": answer,
                        **common,
                    }
                )
            continue
        day = _date_from_path(path)
        for match in NOTE_RE.finditer(text):
            note = match.group("text").strip()
            if not note:
                continue
            migration_key = _key("note", note)
            if migration_key in seen:
                continue
            seen.add(migration_key)
            events.append(
                {
                    "event_id": f"legacy-{migration_key[:24]}-n",
                    "ts": f"{day}T{match.group('time')}:00",
                    "session_id": "legacy-import",
                    "role": "user",
                    "kind": "legacy_note",
                    "text": note,
                    "metadata": {
                        "source": "legacy_note",
                        "migration_key": migration_key,
                        "source_path": str(path.relative_to(user_root)).replace("\\", "/"),
                    },
                }
            )
    return events


def _unparsed_legacy_markdown(user_root: Path) -> list[Path]:
    """Не позволить cleanup удалить вручную изменённый raw, который мы не поняли."""
    candidates: list[Path] = []
    for directory in (
        user_root / "00_raw" / "qna",
        user_root / "00_raw" / "notes",
        user_root / "notes",
    ):
        if directory.exists():
            candidates.extend(sorted(directory.rglob("*.md")))
    raw_root = user_root / "00_raw"
    if raw_root.exists():
        candidates.extend(path for path in raw_root.glob("*.md") if path.is_file())
    unparsed: list[Path] = []
    for path in candidates:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            unparsed.append(path)
            continue
        meaningful = [
            line
            for line in text.splitlines()
            if line.strip() and not line.lstrip().startswith("# ")
        ]
        if meaningful and not QNA_RE.search(text) and not NOTE_RE.search(text):
            unparsed.append(path)
    return unparsed


def _existing_migration_keys(path: Path) -> set[str]:
    keys: set[str] = set()
    if not path.exists():
        return keys
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        metadata = row.get("metadata") if isinstance(row, dict) else None
        if isinstance(metadata, dict) and metadata.get("migration_key"):
            keys.add(str(metadata["migration_key"]))
    return keys


def _append_legacy_events(user_root: Path, events: list[dict]) -> int:
    destination = user_root / "00_raw" / "sessions" / "legacy-import.jsonl"
    existing = _existing_migration_keys(destination)
    pending = [
        event
        for event in events
        if str(event.get("metadata", {}).get("migration_key")) not in existing
    ]
    if not pending:
        return 0
    old = destination.read_text(encoding="utf-8") if destination.exists() else ""
    encoded = "".join(json.dumps(event, ensure_ascii=False) + "\n" for event in pending)
    _atomic_text(destination, old + encoded)
    final_keys = _existing_migration_keys(destination)
    expected = {
        str(event["metadata"]["migration_key"])
        for event in events
        if isinstance(event.get("metadata"), dict)
    }
    if not expected.issubset(final_keys):
        raise RuntimeError("legacy session verification failed")
    return len(pending)


def _copy_first(candidates: list[Path], destination: Path) -> bool:
    if destination.exists():
        return False
    source = next((path for path in candidates if path.exists() and path.is_file()), None)
    if source is None:
        return False
    _atomic_text(destination, source.read_text(encoding="utf-8"))
    return True


def _aspect(name: str) -> str:
    lowered = name.casefold()
    mapping = {
        "реч": "speech",
        "эмоц": "emotional_regulation",
        "отнош": "relationships",
        "ценност": "values",
        "мотив": "motivation",
        "привыч": "habits",
        "образ": "self_image",
        "триггер": "triggers",
    }
    return next((value for marker, value in mapping.items() if marker in lowered), "character")


def _old_delta_rows(user_root: Path) -> list[dict]:
    paths = [
        user_root / "03_personality" / "deltas.json",
        user_root / "03_personality" / "deltas.jsonl",
        user_root / "05_Общее" / "deltas.json",
        user_root / "05_Общее" / "deltas.jsonl",
    ]
    rows: list[dict] = []
    for path in paths:
        if not path.exists():
            continue
        if path.suffix == ".jsonl":
            for line in path.read_text(encoding="utf-8").splitlines():
                try:
                    value = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(value, dict):
                    rows.append(value)
        else:
            value = _read_json(path, {})
            if isinstance(value, dict) and isinstance(value.get("items"), list):
                rows.extend(item for item in value["items"] if isinstance(item, dict))
            elif isinstance(value, list):
                rows.extend(item for item in value if isinstance(item, dict))
    return rows


def _import_deltas(user_root: Path) -> int:
    destination = user_root / "01_personality" / "deltas.json"
    store = _read_json(destination, {"version": 1, "items": []})
    if not isinstance(store, dict) or not isinstance(store.get("items"), list):
        store = {"version": 1, "items": []}
    existing = {str(item.get("id")) for item in store["items"] if isinstance(item, dict)}
    added = 0
    for row in _old_delta_rows(user_root):
        if row.get("status") in {"pending", "synthesized"} and row.get("summary"):
            summaries = [(str(row.get("aspect") or "character"), str(row["summary"]))]
        else:
            payload = row.get("user_delta") if isinstance(row.get("user_delta"), dict) else row
            summaries = [
                (str(key), str(value))
                for key, value in payload.items()
                if isinstance(value, (str, int, float)) and str(value).strip()
            ]
        for name, summary in summaries:
            source_id = _key("delta", name, summary)
            delta_id = f"legacy-{source_id[:24]}"
            if delta_id in existing:
                continue
            store["items"].append(
                {
                    "id": delta_id,
                    "created_at": str(row.get("created_at") or row.get("ts") or datetime.now().isoformat()),
                    "raw_event_id": str(row.get("raw_event_id") or "legacy-import"),
                    "aspect": _aspect(name),
                    "summary": summary[:800],
                    "quote": str(row.get("quote") or "")[:800],
                    "confidence": float(row.get("confidence") or 0.5),
                    "status": "pending",
                    "synthesized_in": None,
                    "synthesized_at": None,
                }
            )
            existing.add(delta_id)
            added += 1
    if added or not destination.exists():
        _atomic_json(destination, store)
    return added


def _migrate_derived(user_root: Path) -> dict[str, int]:
    copied = {"mood": 0, "about": 0, "face": 0, "deltas": 0}
    mood_target = user_root / "01_mood" / "current.md"
    copied["mood"] = int(
        _copy_first(
            [
                user_root / "03_personality" / "mood.md",
                user_root / "05_Общее" / "mood.md",
                user_root / "01_Мироощущение" / "mood" / "mood.md",
                user_root / "01_mood" / "mood.md",
            ],
            mood_target,
        )
    )
    about_target = user_root / "01_personality" / "about" / "current.md"
    copied["about"] = int(
        _copy_first(
            [
                user_root / "03_personality" / "about.md",
                user_root / "05_Общее" / "about.md",
            ],
            about_target,
        )
    )
    face_target = user_root / "01_personality" / "face"
    for root in (user_root / "03_personality", user_root / "05_Общее"):
        for filename in FACE_FILES:
            source = root / filename
            destination = face_target / filename
            if source.exists() and not destination.exists():
                _atomic_text(destination, source.read_text(encoding="utf-8"))
                copied["face"] += 1
    copied["deltas"] = _import_deltas(user_root)
    return copied


def _legacy_sources(user_root: Path) -> list[Path]:
    result = [
        user_root / "00_raw" / "qna",
        user_root / "00_raw" / "notes",
        user_root / "notes",
    ]
    raw_root = user_root / "00_raw"
    if raw_root.exists():
        result.extend(path for path in raw_root.glob("*.md") if path.is_file())
    result.extend(user_root / name for name in LEGACY_DIRS)
    result.extend(user_root.glob("02_*"))
    mood = user_root / "01_mood"
    result.extend(mood / name for name in MOOD_JUNK)
    return sorted({path for path in result if path.exists()}, key=lambda path: str(path))


def _remove_legacy(user_root: Path) -> int:
    removed = 0
    for path in _legacy_sources(user_root):
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
        removed += 1
    return removed


def _git(vault: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=vault,
        text=True,
        capture_output=True,
        check=check,
    )


def _git_ready(vault: Path) -> bool:
    if not (vault / ".git").exists():
        return False
    result = _git(vault, "rev-parse", "--is-inside-work-tree", check=False)
    return result.returncode == 0


@contextmanager
def _user_transaction(vault: Path, uid: str):
    if not _git_ready(vault):
        yield
        return
    scope = f"users/{uid}"
    _git(vault, "add", "-A", "--", scope, check=False)
    _git(
        vault,
        "commit",
        "-m",
        f"psycho({uid}): before simplified migration",
        "--allow-empty",
        "--",
        scope,
        check=False,
    )
    before = _git(vault, "rev-parse", "HEAD").stdout.strip()
    try:
        yield
    except Exception:
        _git(vault, "checkout", before, "--", scope, check=False)
        _git(vault, "clean", "-fd", scope, check=False)
        raise
    _git(vault, "add", "-A", "--", scope, check=False)
    _git(
        vault,
        "commit",
        "-m",
        f"psycho({uid}): simplified migration",
        "--",
        scope,
        check=False,
    )


def _preview_user(user_root: Path) -> dict:
    events = _legacy_events(user_root)
    keys = {
        str(event["metadata"]["migration_key"])
        for event in events
        if isinstance(event.get("metadata"), dict)
    }
    return {
        "uid": user_root.name,
        "legacy_unique_entries": len(keys),
        "legacy_events": len(events),
        "old_deltas": len(_old_delta_rows(user_root)),
        "remove": [
            str(path.relative_to(user_root)).replace("\\", "/")
            for path in _legacy_sources(user_root)
        ],
    }


def _apply_user(vault: Path, user_root: Path) -> dict:
    with _user_transaction(vault, user_root.name):
        unparsed = _unparsed_legacy_markdown(user_root)
        if unparsed:
            names = ", ".join(
                str(path.relative_to(user_root)).replace("\\", "/")
                for path in unparsed
            )
            raise RuntimeError(f"unparsed legacy Markdown kept: {names}")
        events = _legacy_events(user_root)
        imported = _append_legacy_events(user_root, events)
        derived = _migrate_derived(user_root)
        unique = {
            str(event["metadata"]["migration_key"])
            for event in events
            if isinstance(event.get("metadata"), dict)
        }
        destination = user_root / "00_raw" / "sessions" / "legacy-import.jsonl"
        if not unique.issubset(_existing_migration_keys(destination)):
            raise RuntimeError("migration count verification failed")
        removed = _remove_legacy(user_root)
        for directory in (
            user_root / "00_raw" / "sessions",
            user_root / "01_mood" / "events",
            user_root / "01_personality" / "about" / "versions",
            user_root / "01_personality" / "face",
        ):
            directory.mkdir(parents=True, exist_ok=True)
    return {
        "uid": user_root.name,
        "imported_events": imported,
        "unique_entries": len(unique),
        "derived": derived,
        "removed": removed,
    }


def _handle_graph_settings(vault: Path, apply: bool) -> str | None:
    graph = vault / ".obsidian" / "graph.json"
    if not graph.exists():
        return None
    value = _read_json(graph, None)
    if value == GRAPH_TEMPLATE:
        if apply:
            graph.unlink()
        return "removed unchanged graph template" if apply else "will remove unchanged graph template"
    return "WARNING: .obsidian/graph.json was modified manually and will be kept"


def _handle_old_manifest(vault: Path, apply: bool) -> str | None:
    manifest = vault / ".psycho" / "manifest.json"
    if not manifest.exists():
        return None
    if apply:
        manifest.unlink()
        return "removed obsolete .psycho/manifest.json"
    return "will remove obsolete .psycho/manifest.json"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--vault", type=Path, default=Path(os.getenv("VAULT_PATH", "/vault")))
    args = parser.parse_args(argv)
    vault = args.vault.resolve()
    users_root = vault / "users"
    users = sorted(
        (path for path in users_root.iterdir() if path.is_dir() and path.name.isdigit()),
        key=lambda path: int(path.name),
    ) if users_root.exists() else []
    report: dict = {
        "mode": "apply" if args.apply else "preview",
        "vault": str(vault),
        "users": [],
    }
    try:
        for user_root in users:
            report["users"].append(
                _apply_user(vault, user_root) if args.apply else _preview_user(user_root)
            )
        graph_status = _handle_graph_settings(vault, args.apply)
        if graph_status:
            report["graph_settings"] = graph_status
        manifest_status = _handle_old_manifest(vault, args.apply)
        if manifest_status:
            report["manifest"] = manifest_status
    except Exception as exc:
        report["error"] = repr(exc)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
