"""Atomic write helpers.

Все критичные файлы в vault (JSON state, концепты, manifest) пишутся через
``atomic_write_text`` / ``atomic_write_json``: контент уходит в ``<path>.tmp``,
fsync на ручку, потом ``os.replace(tmp, path)`` — атомарная подмена на NTFS и
ext4. Это убирает риск битых файлов при:

* kill контейнера в момент записи;
* YandexDisk-pull, который иначе мог бы засинкать полу-записанный файл.

Append-only логи (``raw/YYYY-MM-DD.md``, ``.psycho/log.md``) не используют этот
модуль — там append безопасен сам по себе.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def atomic_write_text(path: Path, content: str, encoding: str = "utf-8") -> None:
    """Записать строку в файл атомарно.

    Создаёт родительские директории при необходимости. Если запись прервалась
    (kill, отказ диска) — целевой файл остаётся прежним; недозаписанный ``.tmp``
    может остаться, его безопасно удалить вручную.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    # Если предыдущий запуск упал между write и replace — снесём огрызок.
    if tmp.exists():
        try:
            tmp.unlink()
        except OSError:
            pass
    with open(tmp, "w", encoding=encoding, newline="\n") as f:
        f.write(content)
        f.flush()
        try:
            os.fsync(f.fileno())
        except OSError:
            # На Windows fsync для текстовых файлов может вернуть ошибку —
            # сам replace всё равно атомарен, продолжаем.
            pass
    os.replace(tmp, path)


def atomic_write_json(path: Path, obj: Any, indent: int = 2) -> None:
    """Записать JSON в файл атомарно. ``ensure_ascii=False`` для кириллицы."""
    text = json.dumps(obj, ensure_ascii=False, indent=indent)
    atomic_write_text(path, text + "\n")
