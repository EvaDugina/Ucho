"""Переиндексация общей библиотеки из сохранённых исходников.

Без ``--apply`` команда выполняет только preview. Запускать при остановленном
боте внутри его Docker-образа:

``docker compose run --rm bot python scripts/reindex_books.py``
``docker compose run --rm bot python scripts/reindex_books.py --apply``
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from bot import books, userctx, vault
from bot.atomic import atomic_write_json


def _source_format(metadata: dict) -> str:
    book_id = str(metadata.get("id") or "")
    directory = vault.books_dir() / book_id
    sources = sorted(
        path
        for path in directory.glob("source.*")
        if path.is_file() and not path.is_symlink() and not directory.is_symlink()
    )
    if len(sources) == 1:
        return sources[0].suffix.casefold().lstrip(".")
    return str(metadata.get("source_format") or "").casefold().lstrip(".")


def _state_plan(user_root: Path, deleted_ids: set[str]) -> dict | None:
    state_path = user_root / "_state.json"
    if not state_path.exists():
        return None
    if state_path.is_symlink() or user_root.is_symlink():
        raise RuntimeError(f"unsafe user state: {state_path}")
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"corrupt user state: {state_path}") from exc
    if not isinstance(state, dict):
        raise RuntimeError(f"invalid user state root: {state_path}")
    book_state = state.get("books")
    if not isinstance(book_state, dict):
        return None
    disabled_before = [str(value) for value in book_state.get("disabled") or []]
    scores_before = book_state.get("scores")
    scores_before = scores_before if isinstance(scores_before, dict) else {}
    pending = book_state.get("pending_reminder")
    pending_id = str(pending.get("book_id") or "") if isinstance(pending, dict) else ""
    removed_disabled = sorted(set(disabled_before) & deleted_ids)
    removed_scores = sorted(set(str(value) for value in scores_before) & deleted_ids)
    clear_pending = pending_id in deleted_ids
    if not removed_disabled and not removed_scores and not clear_pending:
        return None
    updated = json.loads(json.dumps(state, ensure_ascii=False))
    updated_books = updated["books"]
    updated_books["disabled"] = sorted(
        value for value in set(disabled_before) if value not in deleted_ids
    )
    updated_books["scores"] = {
        str(key): value
        for key, value in scores_before.items()
        if str(key) not in deleted_ids
    }
    if clear_pending:
        updated_books["pending_reminder"] = None
    return {
        "uid": user_root.name,
        "removed_disabled": removed_disabled,
        "removed_scores": removed_scores,
        "cleared_pending": clear_pending,
        "_state": updated,
    }


def _delete_txt_book(book_id: str) -> None:
    if not books.BOOK_ID_RE.fullmatch(book_id):
        raise RuntimeError(f"unsafe book id: {book_id}")
    root = vault.books_dir().resolve()
    directory = (root / book_id).resolve()
    if directory.parent != root or directory.name != book_id:
        raise RuntimeError(f"unsafe book path: {directory}")
    if not directory.exists():
        return
    if directory.is_symlink():
        raise RuntimeError(f"refusing symlinked book: {directory}")
    with vault.books_git_wrap(f"remove unsupported TXT {book_id}"):
        shutil.rmtree(directory)


def _apply_state(plan: dict) -> None:
    uid = int(plan["uid"])
    userctx.set_user(uid)
    with vault.git_wrap("remove unsupported TXT state"):
        atomic_write_json(userctx.user_root() / "_state.json", plan["_state"])


def build_plan() -> dict:
    records = books.scan_books()
    txt_ids = {
        str(metadata["id"])
        for metadata in records
        if _source_format(metadata) == "txt"
    }
    supported: list[dict] = []
    errors: list[dict] = []
    untouched: list[dict] = []
    for metadata in records:
        book_id = str(metadata["id"])
        source_format = _source_format(metadata)
        if source_format == "txt":
            continue
        if source_format not in books.SUPPORTED_FORMATS:
            untouched.append(
                {
                    "id": book_id,
                    "format": source_format or "unknown",
                    "reason": "unsupported format kept but hidden",
                }
            )
            continue
        try:
            supported.append(books.inspect_saved_book(book_id))
        except Exception as exc:
            errors.append({"id": book_id, "error": str(exc)})

    users: list[dict] = []
    users_root = vault.books_dir().parent / "users"
    if users_root.exists():
        for user_root in sorted(users_root.iterdir(), key=lambda path: path.name):
            if not user_root.is_dir() or not user_root.name.isdigit():
                continue
            try:
                state = _state_plan(user_root, txt_ids)
            except Exception as exc:
                errors.append({"uid": user_root.name, "error": str(exc)})
                continue
            if state:
                state.pop("_state", None)
                users.append(state)
    return {
        "supported": supported,
        "delete_txt": sorted(txt_ids),
        "users": users,
        "untouched": untouched,
        "errors": errors,
    }


def run(*, apply: bool) -> dict:
    preview = build_plan()
    report = {"mode": "apply" if apply else "preview", **preview}
    if not apply:
        return report

    report["reindexed"] = []
    report["deleted_txt"] = []
    report["updated_users"] = []
    errors = list(report["errors"])

    for item in preview["supported"]:
        if item.get("status") != "reindex":
            continue
        try:
            result = books.reindex_saved_book(str(item["id"]))
            report["reindexed"].append(result)
        except Exception as exc:
            errors.append({"id": item.get("id"), "error": str(exc)})

    txt_ids = set(str(value) for value in preview["delete_txt"])
    users_root = vault.books_dir().parent / "users"
    state_cleanup_failed = False
    if txt_ids and users_root.exists():
        for user_root in sorted(users_root.iterdir(), key=lambda path: path.name):
            if not user_root.is_dir() or not user_root.name.isdigit():
                continue
            try:
                state = _state_plan(user_root, txt_ids)
                if state:
                    public = {key: value for key, value in state.items() if key != "_state"}
                    _apply_state(state)
                    report["updated_users"].append(public)
            except Exception as exc:
                state_cleanup_failed = True
                errors.append({"uid": user_root.name, "error": str(exc)})
    if state_cleanup_failed:
        errors.append(
            {
                "delete_txt": sorted(txt_ids),
                "error": "TXT deletion skipped because user state cleanup failed",
            }
        )
    else:
        for book_id in sorted(txt_ids):
            try:
                _delete_txt_book(book_id)
                report["deleted_txt"].append(book_id)
            except Exception as exc:
                errors.append({"id": book_id, "error": str(exc)})
    report["errors"] = errors
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    report = run(apply=args.apply)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if report["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
