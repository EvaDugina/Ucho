"""Manifest mtime/size файлов, которые мы пишем в vault.

Каждый раз когда `save_concept` или другая ответственная функция меняет файл,
она вызывает ``record(path)`` — мы сохраняем актуальные mtime/size. Перед
следующей записью вызывается ``check_drift(path)``:

* если файла нет в манифесте — это новый файл, drift отсутствует;
* если mtime/size совпадает с записанным — drift отсутствует (мы знаем,
  что файл в том состоянии, в котором мы его оставили);
* если не совпадает — кто-то правил извне (Obsidian, YandexDisk-pull,
  другой инстанс бота) → возвращаем True, вызывающая сторона решает что
  делать (мы сейчас отказываемся перезаписывать и пишем warn в log.md).

Манифест хранится в ``<vault>/.psycho/manifest.json``. Атомарная запись —
через ``bot.atomic``.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from .atomic import atomic_write_json
from .config import MANIFEST_PATH, PSYCHO_META_DIR, VAULT_PATH

log = logging.getLogger(__name__)

_VERSION = "1.0"
_EMPTY: dict[str, Any] = {"version": _VERSION, "files": {}}


def _read() -> dict[str, Any]:
    if not MANIFEST_PATH.exists():
        return dict(_EMPTY)
    try:
        data = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        log.exception("manifest corrupted, resetting")
        return dict(_EMPTY)
    if not isinstance(data, dict) or "files" not in data:
        log.warning("manifest has unexpected shape, resetting")
        return dict(_EMPTY)
    return data


def _write(data: dict[str, Any]) -> None:
    PSYCHO_META_DIR.mkdir(parents=True, exist_ok=True)
    atomic_write_json(MANIFEST_PATH, data)


def _rel(path: Path) -> str:
    """Относительный к vault ключ в манифесте. Если вне vault — abs str."""
    try:
        return str(path.resolve().relative_to(VAULT_PATH.resolve())).replace("\\", "/")
    except ValueError:
        return str(path)


def _stat(path: Path) -> dict[str, Any] | None:
    try:
        st = path.stat()
    except OSError:
        return None
    return {"mtime_ns": st.st_mtime_ns, "size": st.st_size}


def record(path: Path) -> None:
    """Запомнить текущее состояние файла после нашей записи."""
    info = _stat(path)
    if info is None:
        log.warning("record: path %s missing, not recording", path)
        return
    data = _read()
    data["files"][_rel(path)] = info
    _write(data)


def forget(path: Path) -> None:
    """Удалить запись о файле (на случай rename / удаления)."""
    data = _read()
    key = _rel(path)
    if key in data["files"]:
        del data["files"][key]
        _write(data)


def check_drift(path: Path) -> bool:
    """True если файл изменён извне с момента нашей последней записи.

    Поведение для разных случаев:
    * Файла нет на диске → False (новый файл — drift не определён).
    * Файла нет в манифесте → False (ещё не отслеживали).
    * mtime_ns/size не совпадают с манифестом → True (внешняя правка).
    """
    info = _stat(path)
    if info is None:
        return False
    data = _read()
    saved = data["files"].get(_rel(path))
    if saved is None:
        return False
    return (
        saved.get("mtime_ns") != info["mtime_ns"]
        or saved.get("size") != info["size"]
    )
