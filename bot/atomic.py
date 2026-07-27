"""Atomic write helpers.

Все критичные JSON/Markdown-файлы в vault пишутся через
``atomic_write_text`` / ``atomic_write_json``: контент уходит в ``<path>.tmp``,
fsync на ручку, потом ``os.replace(tmp, path)`` — атомарная подмена на NTFS и
ext4. Это убирает риск битых файлов при:

* kill контейнера в момент записи;
* git pull/checkout или другой внешний sync, который иначе мог бы увидеть
  полу-записанный файл.

Session-log дописывается отдельно, а производные файлы заменяются атомарно.
"""
from __future__ import annotations

import json
import os
from contextlib import suppress
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
        with suppress(OSError):
            tmp.unlink()
    with open(tmp, "w", encoding=encoding, newline="\n") as f:
        f.write(content)
        f.flush()
        with suppress(OSError):
            os.fsync(f.fileno())
    os.replace(tmp, path)


def atomic_write_json(path: Path, obj: Any, indent: int = 2) -> None:
    """Записать JSON в файл атомарно. ``ensure_ascii=False`` для кириллицы."""
    text = json.dumps(obj, ensure_ascii=False, indent=indent)
    atomic_write_text(path, text + "\n")


def atomic_write_bytes(path: Path, content: bytes) -> None:
    """Атомарно записать бинарный файл (например, исходник книги)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    if tmp.exists():
        with suppress(OSError):
            tmp.unlink()
    with open(tmp, "wb") as handle:
        handle.write(content)
        handle.flush()
        with suppress(OSError):
            os.fsync(handle.fileno())
    os.replace(tmp, path)
