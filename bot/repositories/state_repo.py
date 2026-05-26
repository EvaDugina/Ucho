"""Per-user `_state.json`: question counter and daily marker."""
from __future__ import annotations

import json
import logging
from datetime import datetime

from ..atomic import atomic_write_json
from ..storage import layout
from ..storage.log import append_log

log = logging.getLogger(__name__)


def _load_state() -> dict:
    sf = layout.state_file()
    if sf.exists():
        try:
            return json.loads(sf.read_text(encoding="utf-8"))
        except Exception:
            log.exception("failed to load state, resetting")
            append_log("warn", "state_corrupted", "_state.json unreadable, resetting to 0")
    return {"last_q_num": 0}


def _save_state(state: dict) -> None:
    layout.ensure_layout()
    atomic_write_json(layout.state_file(), state)


def next_q_num() -> int:
    state = _load_state()
    state["last_q_num"] = int(state.get("last_q_num", 0)) + 1
    _save_state(state)
    return state["last_q_num"]


def _today_str(tz_name: str) -> str:
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo(tz_name)).strftime("%Y-%m-%d")
    except Exception:
        return datetime.now().strftime("%Y-%m-%d")


def daily_already_sent(tz_name: str) -> bool:
    return _load_state().get("last_daily_date") == _today_str(tz_name)


def mark_daily_sent(tz_name: str) -> None:
    state = _load_state()
    state["last_daily_date"] = _today_str(tz_name)
    _save_state(state)

