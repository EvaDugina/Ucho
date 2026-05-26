"""Глобальный append-only operation log в `<vault>/.psycho/log.md`."""
from __future__ import annotations

import logging
from datetime import datetime

from ..atomic import atomic_write_text
from ..config import LOG_PATH, PSYCHO_META_DIR

log = logging.getLogger(__name__)

_LOG_MAX_BYTES = 1_000_000


def _rotate_log_if_large() -> None:
    """Усечь log.md, если он перерос порог: оставить последнюю половину."""
    try:
        if LOG_PATH.exists() and LOG_PATH.stat().st_size > _LOG_MAX_BYTES:
            text = LOG_PATH.read_text(encoding="utf-8")
            tail = text[-(_LOG_MAX_BYTES // 2):]
            atomic_write_text(LOG_PATH, "# Operation log (усечён)\n\n" + tail)
    except Exception:
        log.exception("log rotation failed")


def append_log(level: str, op: str, details: str = "") -> None:
    """Append-only лог операций в ``<vault>/.psycho/log.md``."""
    try:
        PSYCHO_META_DIR.mkdir(parents=True, exist_ok=True)
        if not LOG_PATH.exists():
            LOG_PATH.write_text("# Operation log\n\n", encoding="utf-8")
        else:
            _rotate_log_if_large()
        ts = datetime.now().strftime("%Y-%m-%d %H:%M")
        line = f"[{ts}] {level.upper()} {op}"
        if details:
            line += f" — {details}"
        with LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        log.exception("append_log failed")

