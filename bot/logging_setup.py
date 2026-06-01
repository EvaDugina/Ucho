"""Настройка stdout/stderr-логов и опционального файла контейнера."""
from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import TextIO

LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s — %(message)s"
DEFAULT_LOG_MAX_BYTES = 10 * 1024 * 1024
DEFAULT_LOG_BACKUP_COUNT = 5
LOG_FILE_NAME = "bot.log"


def _int_env(name: str, default: int, *, minimum: int = 0) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value >= minimum else default


def _level_from_name(level_name: str) -> int:
    level = getattr(logging, (level_name or "INFO").upper(), None)
    return level if isinstance(level, int) else logging.INFO


def _close_handlers(logger: logging.Logger) -> None:
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()


def configure_logging(
    level_name: str,
    *,
    log_dir: str | os.PathLike[str] | None = None,
    stream: TextIO | None = None,
) -> None:
    """Включить stderr logging и файл ``bot.log``, если задана папка логов.

    Файловый handler best-effort: ошибка создания ``.logs`` не должна валить
    старт бота, потому что Docker logs остаются основным аварийным каналом.
    """
    root = logging.getLogger()
    _close_handlers(root)

    formatter = logging.Formatter(LOG_FORMAT)
    stream_handler = logging.StreamHandler(stream or sys.stderr)
    stream_handler.setFormatter(formatter)
    root.addHandler(stream_handler)
    root.setLevel(_level_from_name(level_name))

    raw_log_dir = log_dir if log_dir is not None else os.getenv("CONTAINER_LOG_DIR")
    if not raw_log_dir:
        return
    target_dir = Path(raw_log_dir).expanduser()

    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            target_dir / LOG_FILE_NAME,
            maxBytes=_int_env(
                "CONTAINER_LOG_MAX_BYTES",
                DEFAULT_LOG_MAX_BYTES,
                minimum=1,
            ),
            backupCount=_int_env(
                "CONTAINER_LOG_BACKUP_COUNT",
                DEFAULT_LOG_BACKUP_COUNT,
                minimum=0,
            ),
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)
    except OSError as exc:
        root.warning("container log file is unavailable: %s", exc)
