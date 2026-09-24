"""Per-user `_state.json`: question counter, daily marker and reminder plan."""
from __future__ import annotations

import json
import logging
import random
from datetime import date, datetime, timedelta

from ..atomic import atomic_write_json
from ..storage import layout
from ..storage.log import append_log

log = logging.getLogger(__name__)


def _load_state() -> dict:
    sf = layout.state_file()
    if sf.is_symlink():
        log.error("refusing symlinked _state.json")
        return {"last_q_num": 0}
    if sf.exists():
        try:
            data = json.loads(sf.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
            raise ValueError("_state.json root must be an object")
        except Exception:
            log.exception("failed to load state, resetting")
            append_log("warn", "state_corrupted", "_state.json unreadable, resetting to 0")
    return {"last_q_num": 0}


def _save_state(state: dict) -> None:
    layout.ensure_layout()
    atomic_write_json(layout.state_file(), state)


def next_q_num() -> int:
    state = _load_state()
    try:
        current = max(0, int(state.get("last_q_num", 0)))
    except (TypeError, ValueError):
        current = 0
    state["last_q_num"] = current + 1
    _save_state(state)
    return state["last_q_num"]


def _today_str(tz_name: str) -> str:
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo(tz_name)).strftime("%Y-%m-%d")
    except Exception:
        return datetime.now().strftime("%Y-%m-%d")


def daily_question_due(
    tz_name: str,
    min_days: int = 4,
    max_days: int = 7,
    *,
    day: str | None = None,
) -> bool:
    """Разрешить первый вопрос или вопрос в сохранённую следующую дату."""
    state = _load_state()
    if not state.get("last_daily_date"):
        return True
    schedule = ensure_daily_schedule(tz_name, min_days, max_days)
    if not schedule:
        return False
    try:
        next_day = date.fromisoformat(str(schedule["next_date"]))
        target_day = date.fromisoformat(_day_or_today(tz_name, day))
    except ValueError:
        log.warning("invalid daily schedule; scheduled question remains blocked")
        return False
    return target_day >= next_day


def _day_or_today(tz_name: str, day: str | None = None) -> str:
    return day or _today_str(tz_name)


def _interval_bounds(min_days: int, max_days: int) -> tuple[int, int]:
    lower = max(1, int(min_days))
    upper = max(lower, int(max_days))
    return lower, upper


def _schedule_from_state(state: dict, min_days: int, max_days: int) -> dict:
    lower, upper = _interval_bounds(min_days, max_days)
    try:
        last_day = date.fromisoformat(str(state.get("last_daily_date") or ""))
        interval_days = int(state.get("daily_interval_days"))
        next_day = date.fromisoformat(str(state.get("next_daily_date") or ""))
        followup_day = date.fromisoformat(str(state.get("unanswered_followup_date") or ""))
    except (TypeError, ValueError):
        return {}
    try:
        q_num = int(state.get("last_daily_q_num"))
    except (TypeError, ValueError):
        q_num = None
    if not lower <= interval_days <= upper:
        return {}
    if next_day != last_day + timedelta(days=interval_days):
        return {}
    if followup_day != next_day - timedelta(days=1):
        return {}
    return {
        "last_date": last_day.isoformat(),
        "interval_days": interval_days,
        "next_date": next_day.isoformat(),
        "followup_date": followup_day.isoformat(),
        "q_num": q_num,
        "followup_sent": (
            q_num is not None and state.get("unanswered_followup_sent_q_num") == q_num
        ),
        "followup_sent_at": state.get("unanswered_followup_sent_at"),
    }


def _write_daily_schedule(
    state: dict,
    *,
    last_day: date,
    interval_days: int,
) -> dict:
    next_day = last_day + timedelta(days=interval_days)
    state["daily_interval_days"] = interval_days
    state["next_daily_date"] = next_day.isoformat()
    state["unanswered_followup_date"] = (next_day - timedelta(days=1)).isoformat()
    state.pop("unanswered_followup_sent_q_num", None)
    state.pop("unanswered_followup_sent_at", None)
    return state


def ensure_daily_schedule(
    tz_name: str,
    min_days: int = 4,
    max_days: int = 7,
) -> dict:
    """Вернуть устойчивое расписание, один раз выбрав его для legacy-state."""
    state = _load_state()
    if not state.get("last_daily_date"):
        return {}
    existing = _schedule_from_state(state, min_days, max_days)
    if existing:
        return existing
    try:
        last_day = date.fromisoformat(str(state.get("last_daily_date")))
    except (TypeError, ValueError):
        log.warning("invalid last daily record; cannot plan next question")
        return {}
    lower, upper = _interval_bounds(min_days, max_days)
    _write_daily_schedule(
        state,
        last_day=last_day,
        interval_days=random.randint(lower, upper),
    )
    _save_state(state)
    return _schedule_from_state(state, lower, upper)


def daily_schedule(
    tz_name: str,
    min_days: int = 4,
    max_days: int = 7,
) -> dict:
    """Текущее сохранённое расписание без повторного случайного выбора."""
    del tz_name
    return _schedule_from_state(_load_state(), min_days, max_days)


def unanswered_followup_due(
    tz_name: str,
    min_days: int = 4,
    max_days: int = 7,
    *,
    day: str | None = None,
) -> bool:
    schedule = ensure_daily_schedule(tz_name, min_days, max_days)
    if not schedule or schedule.get("q_num") is None or schedule.get("followup_sent"):
        return False
    return schedule.get("followup_date") == _day_or_today(tz_name, day)


def mark_unanswered_followup_sent(
    tz_name: str,
    *,
    q_num: int,
    sent_at: object | None = None,
) -> bool:
    """Отметить реплику только если она относится к последнему daily-вопросу."""
    state = _load_state()
    try:
        if int(state.get("last_daily_q_num")) != int(q_num):
            return False
    except (TypeError, ValueError):
        return False
    state["unanswered_followup_sent_q_num"] = int(q_num)
    if isinstance(sent_at, datetime):
        state["unanswered_followup_sent_at"] = sent_at.isoformat(timespec="seconds")
    elif isinstance(sent_at, str) and sent_at:
        state["unanswered_followup_sent_at"] = sent_at
    else:
        try:
            from zoneinfo import ZoneInfo
            now = datetime.now(ZoneInfo(tz_name))
        except Exception:
            now = datetime.now()
        state["unanswered_followup_sent_at"] = now.isoformat(timespec="seconds")
    _save_state(state)
    return True


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


def last_daily_record() -> dict:
    """Последний успешно отправленный автоматический вопрос независимо от даты."""
    state = _load_state()
    day = str(state.get("last_daily_date") or "")
    if not day:
        return {}
    return {
        "date": day,
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
    min_days: int = 4,
    max_days: int = 7,
    day: str | None = None,
) -> dict:
    """Mark the daily question and atomically choose its next schedule."""
    state = _load_state()
    sent_day = date.fromisoformat(_day_or_today(tz_name, day))
    state["last_daily_date"] = sent_day.isoformat()
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
    lower, upper = _interval_bounds(min_days, max_days)
    _write_daily_schedule(
        state,
        last_day=sent_day,
        interval_days=random.randint(lower, upper),
    )
    _save_state(state)
    return _schedule_from_state(state, lower, upper)


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
