"""Evening reminder use case for unanswered daily questions.

Напоминание не создаёт новый вопрос и не пишет граф: оно возвращает человека к
сегодняшнему daily Q, если тот был отправлен до вечернего окна и остался без
user-answer на свой q_num.
"""
from __future__ import annotations

import logging
import random
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from aiogram import Bot

from .. import moods, session, session_log, userctx, vault
from ..config import DAILY_REMINDER_END, DAILY_REMINDER_START, DAILY_TZ
from ..errors import LLMError
from ..llm import remind_presence
from .daily_service import daily_targets
from .session_messages import send_question

log = logging.getLogger(__name__)

_FALLBACK_REMINDER = "Я всё ещё здесь. Жду твой ответ."


@dataclass(frozen=True)
class ReminderCandidate:
    uid: int
    day: str
    q_num: int
    session_id: str
    domain: str
    question: str


def _tz() -> ZoneInfo:
    try:
        return ZoneInfo(DAILY_TZ)
    except Exception:
        return ZoneInfo("Europe/Moscow")


def parse_hhmm(value: str, fallback: str) -> time:
    raw = (value or fallback).strip()
    try:
        hh, mm = raw.split(":", 1)
        return time(hour=int(hh), minute=int(mm))
    except Exception:
        log.warning("bad reminder time %r, falling back to %s", value, fallback)
        hh, mm = fallback.split(":", 1)
        return time(hour=int(hh), minute=int(mm))


def reminder_start_time() -> time:
    return parse_hhmm(DAILY_REMINDER_START, "23:00")


def reminder_end_time() -> time:
    return parse_hhmm(DAILY_REMINDER_END, "01:00")


def _coerce_now(now: datetime | None = None) -> datetime:
    tz = _tz()
    if now is None:
        return datetime.now(tz)
    if now.tzinfo is None:
        return now.replace(tzinfo=tz)
    return now.astimezone(tz)


def local_now() -> datetime:
    return _coerce_now()


def _parse_day(day: str) -> date:
    return date.fromisoformat(day)


def reminder_window(day: str) -> tuple[datetime, datetime]:
    """Bounds for reminder day D: usually D 23:00 → D+1 01:00."""
    tz = _tz()
    d = _parse_day(day)
    start_t = reminder_start_time()
    end_t = reminder_end_time()
    start = datetime.combine(d, start_t, tzinfo=tz)
    end_day = d + timedelta(days=1) if end_t <= start_t else d
    end = datetime.combine(end_day, end_t, tzinfo=tz)
    return start, end


def reminder_day_for_now(now: datetime | None = None) -> str | None:
    """Return the daily date whose reminder window contains now."""
    current = _coerce_now(now)
    for d in (current.date(), current.date() - timedelta(days=1)):
        day = d.isoformat()
        start, end = reminder_window(day)
        if start <= current < end:
            return day
    return None


def choose_batch_time(day: str, now: datetime | None = None) -> datetime | None:
    """Pick one random dispatch time inside the remaining reminder window."""
    current = _coerce_now(now)
    start, end = reminder_window(day)
    lower = max(start, current)
    if lower >= end:
        return None
    seconds = int((end - lower).total_seconds())
    if seconds <= 0:
        return None
    return lower + timedelta(seconds=random.randint(0, max(0, seconds - 1)))


def _parse_dt(value: object | None) -> datetime | None:
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str) and value:
        try:
            dt = datetime.fromisoformat(value)
        except ValueError:
            return None
    else:
        return None
    tz = _tz()
    if dt.tzinfo is None:
        return dt.replace(tzinfo=tz)
    return dt.astimezone(tz)


def _daily_question_event(q_num: int, session_id: str) -> dict | None:
    for event in session_log.session_events(session_id):
        if event.get("role") != "assistant" or event.get("kind") != "question":
            continue
        try:
            event_q_num = int(event.get("q_num"))
        except (TypeError, ValueError):
            continue
        if event_q_num == q_num:
            return event
    return None


def _is_answered(q_num: int) -> bool:
    entry = session_log.find_question_by_q_num(q_num)
    return bool(entry and entry.get("answered"))


def _candidate_for_current_user(day: str) -> ReminderCandidate | None:
    record = vault.daily_record(DAILY_TZ, day=day)
    try:
        q_num = int(record.get("q_num"))
    except (TypeError, ValueError):
        return None
    session_id = str(record.get("session_id") or "")
    if not session_id:
        return None
    sent_at = _parse_dt(record.get("sent_at"))
    start, _ = reminder_window(day)
    if sent_at is None or sent_at >= start:
        return None
    plan = vault.daily_reminder_plan(DAILY_TZ, day=day)
    if plan.get("done"):
        return None
    event = _daily_question_event(q_num, session_id)
    if event is None or _is_answered(q_num):
        return None
    return ReminderCandidate(
        uid=int(userctx.current_uid() or 0),
        day=day,
        q_num=q_num,
        session_id=session_id,
        domain=str(event.get("domain") or "everyday"),
        question=str(event.get("text") or ""),
    )


def collect_unanswered_daily_targets(
    *,
    day: str | None = None,
    now: datetime | None = None,
) -> list[ReminderCandidate]:
    reminder_day = day or reminder_day_for_now(now)
    if not reminder_day:
        return []
    out: list[ReminderCandidate] = []
    for uid in daily_targets():
        userctx.set_user(uid)
        candidate = _candidate_for_current_user(reminder_day)
        if candidate is not None:
            out.append(candidate)
    return out


def _planned_at_for_current_user(day: str) -> datetime | None:
    plan = vault.daily_reminder_plan(DAILY_TZ, day=day)
    if not plan or plan.get("done"):
        return None
    return _parse_dt(plan.get("at"))


def pending_plan_time(
    *,
    day: str | None = None,
    now: datetime | None = None,
) -> datetime | None:
    reminder_day = day or reminder_day_for_now(now)
    if not reminder_day:
        return None
    times: list[datetime] = []
    for uid in daily_targets():
        userctx.set_user(uid)
        at = _planned_at_for_current_user(reminder_day)
        if at is not None:
            times.append(at)
    return min(times) if times else None


async def ensure_daily_reminder_plan(
    *,
    now: datetime | None = None,
) -> tuple[datetime | None, list[ReminderCandidate]]:
    """Create one shared reminder time for today's unanswered daily targets."""
    reminder_day = reminder_day_for_now(now)
    if not reminder_day:
        return None, []
    existing_at = pending_plan_time(day=reminder_day, now=now)
    if existing_at is not None:
        return existing_at, collect_unanswered_daily_targets(day=reminder_day, now=now)

    candidates = collect_unanswered_daily_targets(day=reminder_day, now=now)
    if not candidates:
        return None, []
    at = choose_batch_time(reminder_day, now=now)
    if at is None:
        return None, []
    for candidate in candidates:
        userctx.set_user(candidate.uid)
        vault.mark_daily_reminder_planned(DAILY_TZ, at, day=reminder_day)
    log.info(
        "daily reminder planned: day=%s at=%s targets=%s",
        reminder_day,
        at.isoformat(timespec="seconds"),
        len(candidates),
    )
    return at, candidates


def due_planned_targets(
    *,
    now: datetime | None = None,
) -> list[int]:
    current = _coerce_now(now)
    reminder_day = reminder_day_for_now(current)
    if not reminder_day:
        return []
    out: list[int] = []
    for uid in daily_targets():
        userctx.set_user(uid)
        plan = vault.daily_reminder_plan(DAILY_TZ, day=reminder_day)
        at = _parse_dt(plan.get("at"))
        if plan and not plan.get("done") and at is not None and at <= current:
            out.append(uid)
    return out


async def send_daily_reminder(bot: Bot, candidate: ReminderCandidate) -> bool:
    userctx.set_user(candidate.uid)
    current = session.get()
    if session.has_unfinished_answer(current):
        log.info("daily reminder skipped: unfinished answer uid=%s", candidate.uid)
        return False
    if current is None or current.id != candidate.session_id:
        if session.resume(candidate.session_id) is None:
            log.warning("daily reminder skipped: session not found uid=%s", candidate.uid)
            return False
    bot_mood = moods.random_bot_mood()
    try:
        await bot.send_chat_action(candidate.uid, "typing")
    except Exception:
        pass
    try:
        text = await remind_presence(candidate.question, bot_mood=bot_mood)
    except LLMError:
        log.warning("daily reminder LLM error; using fallback uid=%s", candidate.uid)
        text = _FALLBACK_REMINDER
    if not text.strip():
        text = _FALLBACK_REMINDER
    await send_question(
        bot,
        candidate.uid,
        q_num=candidate.q_num,
        mode="probe",
        domain=candidate.domain,
        text=text,
        plain=True,
        bot_mood=bot_mood,
        event_kind="reminder",
    )
    return True


async def send_due_daily_reminders(
    bot: Bot,
    *,
    now: datetime | None = None,
) -> dict[str, int]:
    current = _coerce_now(now)
    reminder_day = reminder_day_for_now(current)
    if not reminder_day:
        return {"sent": 0, "skipped": 0, "errors": 0}
    sent = skipped = errors = 0
    for uid in due_planned_targets(now=current):
        userctx.set_user(uid)
        candidate = _candidate_for_current_user(reminder_day)
        try:
            if candidate is not None and await send_daily_reminder(bot, candidate):
                sent += 1
            else:
                skipped += 1
            vault.mark_daily_reminder_done(DAILY_TZ, day=reminder_day)
            vault.commit_all("daily reminder")
        except Exception:
            errors += 1
            log.exception("daily reminder failed for uid=%s", uid)
    return {"sent": sent, "skipped": skipped, "errors": errors}
