"""Вечерняя книжная цитата вместо текстового напоминания."""
from __future__ import annotations

import html
import logging
import random
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from aiogram import Bot

from .. import books, session, session_log, userctx, vault
from ..config import DAILY_REMINDER_END, DAILY_REMINDER_START, DAILY_TZ
from .daily_service import daily_targets

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ReminderCandidate:
    uid: int
    day: str
    q_num: int
    session_id: str


def _tz() -> ZoneInfo:
    try:
        return ZoneInfo(DAILY_TZ)
    except Exception:
        return ZoneInfo("Europe/Moscow")


def parse_hhmm(value: str, fallback: str) -> time:
    raw = (value or fallback).strip()
    try:
        hours, minutes = raw.split(":", 1)
        return time(hour=int(hours), minute=int(minutes))
    except Exception:
        log.warning("bad reminder time %r, falling back to %s", value, fallback)
        hours, minutes = fallback.split(":", 1)
        return time(hour=int(hours), minute=int(minutes))


def reminder_start_time() -> time:
    return parse_hhmm(DAILY_REMINDER_START, "23:00")


def reminder_end_time() -> time:
    return parse_hhmm(DAILY_REMINDER_END, "01:00")


def _coerce_now(now: datetime | None = None) -> datetime:
    timezone = _tz()
    if now is None:
        return datetime.now(timezone)
    if now.tzinfo is None:
        return now.replace(tzinfo=timezone)
    return now.astimezone(timezone)


def local_now() -> datetime:
    return _coerce_now()


def reminder_window(day: str) -> tuple[datetime, datetime]:
    timezone = _tz()
    reminder_date = date.fromisoformat(day)
    start_at = reminder_start_time()
    end_at = reminder_end_time()
    start = datetime.combine(reminder_date, start_at, tzinfo=timezone)
    end_date = reminder_date + timedelta(days=1) if end_at <= start_at else reminder_date
    return start, datetime.combine(end_date, end_at, tzinfo=timezone)


def reminder_day_for_now(now: datetime | None = None) -> str | None:
    current = _coerce_now(now)
    for candidate_date in (current.date(), current.date() - timedelta(days=1)):
        day = candidate_date.isoformat()
        start, end = reminder_window(day)
        if start <= current < end:
            return day
    return None


def choose_batch_time(day: str, now: datetime | None = None) -> datetime | None:
    current = _coerce_now(now)
    start, end = reminder_window(day)
    lower = max(start, current)
    seconds = int((end - lower).total_seconds())
    if seconds <= 0:
        return None
    return lower + timedelta(seconds=random.randint(0, seconds - 1))


def _parse_dt(value: object | None) -> datetime | None:
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    timezone = _tz()
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone)
    return parsed.astimezone(timezone)


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
    plan = vault.daily_reminder_plan(DAILY_TZ, day=day)
    if sent_at is None or sent_at >= start or plan.get("done") or _is_answered(q_num):
        return None
    return ReminderCandidate(
        uid=int(userctx.current_uid() or 0),
        day=day,
        q_num=q_num,
        session_id=session_id,
    )


def collect_unanswered_daily_targets(
    *,
    day: str | None = None,
    now: datetime | None = None,
) -> list[ReminderCandidate]:
    reminder_day = day or reminder_day_for_now(now)
    if not reminder_day:
        return []
    result: list[ReminderCandidate] = []
    for uid in daily_targets():
        userctx.set_user(uid)
        candidate = _candidate_for_current_user(reminder_day)
        if candidate is not None:
            result.append(candidate)
    return result


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
        planned = _planned_at_for_current_user(reminder_day)
        if planned is not None:
            times.append(planned)
    return min(times) if times else None


async def ensure_daily_reminder_plan(
    *,
    now: datetime | None = None,
) -> tuple[datetime | None, list[ReminderCandidate]]:
    reminder_day = reminder_day_for_now(now)
    if not reminder_day:
        return None, []
    existing_at = pending_plan_time(day=reminder_day, now=now)
    if existing_at is not None:
        return existing_at, collect_unanswered_daily_targets(day=reminder_day, now=now)
    candidates = collect_unanswered_daily_targets(day=reminder_day, now=now)
    if not candidates:
        return None, []
    planned_at = choose_batch_time(reminder_day, now=now)
    if planned_at is None:
        return None, []
    for candidate in candidates:
        userctx.set_user(candidate.uid)
        vault.mark_daily_reminder_planned(DAILY_TZ, planned_at, day=reminder_day)
    return planned_at, candidates


def due_planned_targets(*, now: datetime | None = None) -> list[int]:
    current = _coerce_now(now)
    reminder_day = reminder_day_for_now(current)
    if not reminder_day:
        return []
    result: list[int] = []
    for uid in daily_targets():
        userctx.set_user(uid)
        plan = vault.daily_reminder_plan(DAILY_TZ, day=reminder_day)
        planned = _parse_dt(plan.get("at"))
        if plan and not plan.get("done") and planned is not None and planned <= current:
            result.append(uid)
    return result


async def send_daily_reminder(bot: Bot, candidate: ReminderCandidate) -> bool:
    userctx.set_user(candidate.uid)
    if session.has_unfinished_answer(session.get()):
        log.info("book reminder skipped: unfinished answer uid=%s", candidate.uid)
        return False
    selected = books.choose_for_reminder()
    if selected is None:
        log.info("book reminder skipped: no enabled books uid=%s", candidate.uid)
        return False
    excerpt = books.choose_excerpt(selected["id"])
    if not excerpt:
        log.warning("book reminder skipped: empty book id=%s", selected["id"])
        return False
    title = str(selected.get("title") or selected["id"])
    author = str(selected.get("author") or "Автор не указан")
    text = (
        f"<blockquote>{html.escape(excerpt)}</blockquote>\n"
        f"<i>{html.escape(title)} — {html.escape(author)}</i>"
    )
    sent = await bot.send_message(candidate.uid, text, parse_mode="HTML")
    event = session_log.append_required(
        session_id=candidate.session_id,
        role="assistant",
        kind="book_reminder",
        text=excerpt,
        q_num=candidate.q_num,
        message_id=sent.message_id,
        metadata={
            "book_id": selected["id"],
            "title": title,
            "author": author,
            "source": "daily_reminder",
        },
    )
    books.set_pending_reminder(
        book=selected,
        excerpt=excerpt,
        message_id=sent.message_id,
        raw_event_id=str(event["event_id"]),
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
            vault.commit_all("book reminder")
        except Exception:
            errors += 1
            log.exception("book reminder failed for uid=%s", uid)
    return {"sent": sent, "skipped": skipped, "errors": errors}
