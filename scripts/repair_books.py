"""Проверить и при необходимости восстановить индексы сохранённых книг.

По умолчанию только показывает состояние. Запускать при остановленном боте.
"""
from __future__ import annotations

import argparse
import json

from bot import books


def run(*, apply: bool = False) -> tuple[list[dict], int]:
    results: list[dict] = []
    errors = 0
    for metadata in books.scan_books():
        book_id = str(metadata["id"])
        try:
            item = books.repair_saved_book(book_id) if apply else books.inspect_saved_book(book_id)
        except (OSError, ValueError) as exc:
            errors += 1
            item = {"id": book_id, "status": "error", "reason": str(exc)}
        results.append(item)
    return results, errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Проверить сохранённые книжные индексы")
    parser.add_argument("--apply", action="store_true", help="восстановить повреждённые индексы")
    args = parser.parse_args(argv)
    results, errors = run(apply=args.apply)
    for item in results:
        print(json.dumps(item, ensure_ascii=False))
    print(f"books={len(results)} errors={errors}")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
