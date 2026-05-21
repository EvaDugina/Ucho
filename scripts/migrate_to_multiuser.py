"""Одноразовая миграция: корневой вольт владельца → users/<OWNER_ID>/.

Multi-user раскладка: данные каждого пользователя в `<vault>/users/<uid>/`.
Существующая база владельца лежит в корне — переносим её под его id.
`.psycho/`, `.git/`, `.gitignore`, `users/` НЕ трогаем (глобальные).

Двухфазно:
  python scripts/migrate_to_multiuser.py            # dry-run (план, ничего не двигает)
  python scripts/migrate_to_multiuser.py --apply    # перенос + верификация под git_wrap

Запускать ТОЛЬКО в Docker и при ОСТАНОВЛЕННОМ боте (иначе гонки записи):
  docker compose stop bot
  docker compose run --rm bot python scripts/migrate_to_multiuser.py
  docker compose run --rm bot python scripts/migrate_to_multiuser.py --apply
  docker compose up -d bot

После успешного --apply скрипт можно удалить.
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bot import userctx, vault  # noqa: E402
from bot.config import OWNER_TELEGRAM_ID, VAULT_PATH  # noqa: E402
from bot import graph  # noqa: E402

# Что переносим из корня в users/<owner>/. Остальное (.psycho/.git/.gitignore/users) — нет.
MOVABLE = [
    "concepts", "raw", "profile", "notes", "digests",
    "_index.md", "_state.json", "_session.json",
]

DEST = VAULT_PATH / "users" / str(OWNER_TELEGRAM_ID)


def _count_concept_md(base: Path) -> int:
    cdir = base / "concepts"
    if not cdir.exists():
        return 0
    return sum(1 for p in cdir.rglob("*.md") if not p.name.startswith("_"))


def _present() -> list[str]:
    return [name for name in MOVABLE if (VAULT_PATH / name).exists()]


def _dry_run() -> int:
    items = _present()
    print("=== DRY RUN: migrate root → users/%s ===" % OWNER_TELEGRAM_ID)
    if not items:
        print("Нечего переносить — в корне нет данных владельца.")
        return 0
    print(f"Будет перенесено в {DEST}:")
    for name in items:
        src = VAULT_PATH / name
        kind = "dir" if src.is_dir() else "file"
        print(f"  - {name} ({kind})")
    print(f"\nКонцептов (.md) в корне: {_count_concept_md(VAULT_PATH)}")
    if DEST.exists() and any(DEST.iterdir()):
        print(f"\n⚠ {DEST} уже существует и непуста — apply откажет (похоже, миграция уже была).")
    print("\nДля применения: python scripts/migrate_to_multiuser.py --apply")
    return 0


def _verify(before_count: int) -> bool:
    """Проверка успешности после переноса. Возвращает True если PASS."""
    ok = True

    # 1. Целевые на месте, источник пуст.
    for name in MOVABLE:
        src = VAULT_PATH / name
        if src.exists():
            print(f"FAIL: источник всё ещё существует: {name}")
            ok = False

    # 2. Счёт концептов сохранился.
    after_count = _count_concept_md(DEST)
    if after_count != before_count:
        print(f"FAIL: концептов было {before_count}, стало {after_count}")
        ok = False
    else:
        print(f"OK: концептов {after_count} (совпадает)")

    # 3. Бот видит данные владельца под его user-root.
    userctx.set_user(OWNER_TELEGRAM_ID)
    try:
        parsed = sum(len(graph.find_concepts(domain=d, limit=10_000)) for d in graph.DOMAINS) \
            if hasattr(graph, "DOMAINS") else None
    except Exception as exc:
        print(f"FAIL: find_concepts под владельцем упал: {exc!r}")
        ok = False
        parsed = None
    # graph.DOMAINS может не быть — берём из config
    if parsed is None:
        from bot.config import DOMAINS
        parsed = sum(len(graph.find_concepts(domain=d, limit=10_000)) for d in DOMAINS)
    if parsed != before_count:
        print(f"FAIL: find_concepts вернул {parsed}, ожидалось {before_count}")
        ok = False
    else:
        print(f"OK: find_concepts под владельцем вернул {parsed}")

    # Примечание: коммит/атомарность обеспечивает git_wrap (add -A + commit
    # после успешного блока; на исключение — git reset --hard). Поэтому проверять
    # «git status чист» ВНУТРИ транзакции нельзя — статус закономерно грязный.

    return ok


def _apply() -> int:
    items = _present()
    if not items:
        print("Нечего переносить.")
        return 0
    if DEST.exists() and any(DEST.iterdir()):
        print(f"ОТКАЗ: {DEST} уже непуста — похоже, миграция уже выполнена.")
        return 1

    before = _count_concept_md(VAULT_PATH)
    print(f"=== APPLY: переносим {len(items)} элементов, концептов {before} ===")

    try:
        with vault.git_wrap("migrate_to_multiuser"):
            DEST.mkdir(parents=True, exist_ok=True)
            for name in items:
                src = VAULT_PATH / name
                dst = DEST / name
                shutil.move(str(src), str(dst))
                print(f"  moved {name}")
            # верификация внутри транзакции: на FAIL бросаем — git_wrap откатит
            if not _verify(before):
                raise RuntimeError("verification FAILED — откатываю миграцию")
    except Exception as exc:
        print(f"\nМиграция ОТМЕНЕНА (откат через git): {exc!r}")
        return 1

    print("\nМиграция УСПЕШНА. Можно удалить scripts/migrate_to_multiuser.py.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="выполнить перенос (иначе dry-run)")
    args = parser.parse_args()
    return _apply() if args.apply else _dry_run()


if __name__ == "__main__":
    raise SystemExit(main())
