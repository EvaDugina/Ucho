"""Per-user `_state.json`: question counter, daily marker and reminder plan."""
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


def _day_or_today(tz_name: str, day: str | None = None) -> str:
    return day or _today_str(tz_name)


def daily_record(tz_name: str, day: str | None = None) -> dict:
    """Return today's/explicit day's daily-question metadata, if present."""
    target_day = _day_or_today(tz_name, day)
    state = _load_state()
    if state.get("last_daily_date") != target_day:
        return {}
    return {
        "date": target_day,
        "q_num": state.get("last_daily_q_num"),
        "session_id": state.get("last_daily_session_id"),
        "sent_at": state.get("last_daily_sent_at"),
    }


def mark_daily_sent_details(
    tz_name: str,
    *,
    q_num: int | None = None,
    session_id: str | None = None,
    sent_at: object | None = None,
) -> None:
    """Mark the daily question and keep enough metadata for late reminders."""
    state = _load_state()
    state["last_daily_date"] = _today_str(tz_name)
    if q_num is not None:
        state["last_daily_q_num"] = int(q_num)
    if session_id:
        state["last_daily_session_id"] = str(session_id)
    if isinstance(sent_at, datetime):
        state["last_daily_sent_at"] = sent_at.isoformat(timespec="seconds")
    elif isinstance(sent_at, str) and sent_at:
        state["last_daily_sent_at"] = sent_at
    else:
        try:
            from zoneinfo import ZoneInfo
            state["last_daily_sent_at"] = datetime.now(ZoneInfo(tz_name)).isoformat(timespec="seconds")
        except Exception:
            state["last_daily_sent_at"] = datetime.now().isoformat(timespec="seconds")
    _save_state(state)


def daily_reminder_plan(tz_name: str, day: str | None = None) -> dict:
    """Return reminder plan for a daily day (not necessarily current date)."""
    target_day = _day_or_today(tz_name, day)
    state = _load_state()
    if state.get("daily_reminder_date") != target_day:
        return {}
    return {
        "date": target_day,
        "at": state.get("daily_reminder_at"),
        "done": state.get("daily_reminder_done_date") == target_day,
    }


def mark_daily_reminder_planned(
    tz_name: str,
    reminder_at: object,
    *,
    day: str | None = None,
) -> None:
    target_day = _day_or_today(tz_name, day)
    state = _load_state()
    state["daily_reminder_date"] = target_day
    if isinstance(reminder_at, datetime):
        state["daily_reminder_at"] = reminder_at.isoformat(timespec="seconds")
    else:
        state["daily_reminder_at"] = str(reminder_at)
    if state.get("daily_reminder_done_date") == target_day:
        state.pop("daily_reminder_done_date", None)
    _save_state(state)


def mark_daily_reminder_done(tz_name: str, *, day: str | None = None) -> None:
    state = _load_state()
    state["daily_reminder_done_date"] = _day_or_today(tz_name, day)
    _save_state(state)
