"""Автоматический вопрос раз в несколько дней без мировоззренческого графа."""
from __future__ import annotations

import logging
import random
from contextlib import suppress

from aiogram import Bot

from .. import books, session, session_log, userctx, users, vault
from ..config import (
    DAILY_INTERVAL_MAX_DAYS,
    DAILY_INTERVAL_MIN_DAYS,
    DAILY_TZ,
    DOMAINS,
)
from ..errors import LLMError
from ..llm import UNANSWERED_MOODS, ask_next, generate_unanswered_followup
from .session_messages import send_plain_session_message, send_question

log = logging.getLogger(__name__)


def daily_targets() -> list[int]:
    return sorted(users.allowed_ids())


def _previous_unanswered_daily() -> dict | None:
    record = vault.last_daily_record()
    try:
        q_num = int(record.get("q_num"))
    except (TypeError, ValueError):
        return None
    question = session_log.find_question_by_q_num(q_num)
    if not question or question.get("answered") or not str(question.get("text") or "").strip():
        return None
    return question


async def _send_unanswered_followup(
    bot: Bot,
    chat_id: int,
    *,
    previous: dict,
    next_question_date: str,
) -> bool:
    mood = random.choice(UNANSWERED_MOODS)
    last_user_session = session_log.latest_user_session_transcript(
        min_chars=4,
        max_chars=24_000,
    )
    try:
        text = await generate_unanswered_followup(
            unanswered_question=str(previous["text"]),
            last_user_session=last_user_session,
            mood=mood,
        )
        await send_plain_session_message(
            bot,
            chat_id,
            session_id=str(previous["session_id"]),
            q_num=int(previous["q_num"]),
            domain=str(previous.get("domain") or ""),
            text=text,
            event_kind="unanswered_followup",
            metadata={
                "source": "scheduled_unanswered_followup",
                "mood": mood,
                "next_question_date": next_question_date,
                "unanswered_q_num": previous.get("q_num"),
                "unanswered_session_id": previous.get("session_id"),
            },
        )
        if not vault.mark_unanswered_followup_sent(
            DAILY_TZ,
            q_num=int(previous["q_num"]),
        ):
            log.warning("unanswered followup state changed before mark uid=%s", chat_id)
        return True
    except Exception:
        log.exception("scheduled unanswered followup failed uid=%s", chat_id)
        return False


async def send_unanswered_followup_if_due(bot: Bot, uid: int) -> bool:
    """Отправить одну реплику за день до следующего вопроса, если ответ не пришёл."""
    if not users.is_allowed(uid):
        log.warning("unanswered followup skipped: uid=%s is not allowed", uid)
        return False
    userctx.set_user(uid)
    async with session.lock_for(uid):
        if not vault.unanswered_followup_due(
            DAILY_TZ,
            DAILY_INTERVAL_MIN_DAYS,
            DAILY_INTERVAL_MAX_DAYS,
        ):
            return False
        previous = _previous_unanswered_daily()
        if previous is None:
            return False
        schedule = vault.daily_schedule(
            DAILY_TZ,
            DAILY_INTERVAL_MIN_DAYS,
            DAILY_INTERVAL_MAX_DAYS,
        )
        next_question_date = str(schedule.get("next_date") or "")
        if not next_question_date:
            return False
        return await _send_unanswered_followup(
            bot,
            uid,
            previous=previous,
            next_question_date=next_question_date,
        )


async def _send_next_question(bot: Bot, chat_id: int, domain: str | None = None) -> int | None:
    selected_domain = domain if domain in DOMAINS else random.choice(tuple(DOMAINS))
    with suppress(Exception):
        await bot.send_chat_action(chat_id, "typing")

    try:
        result = await ask_next(
            domain=selected_domain,
            hint=None,
        )
    except LLMError:
        log.warning("daily ask_next LLM error; user reply suppressed")
        return None

    current = session.start(domain=selected_domain)
    q_num = vault.next_q_num()
    question = str(result["question"])
    session.set_question(
        question,
        domain=selected_domain,
        q_num=q_num,
        metadata={"source": "daily", "topic": selected_domain},
    )
    current.main_question = question
    session.persist()
    await send_question(
        bot,
        chat_id,
        q_num=q_num,
        domain=selected_domain,
        text=question,
        metadata={"source": "daily", "topic": selected_domain},
    )
    books.expire_pending_reminder()
    return q_num


async def send_daily_question(bot: Bot, uid: int) -> bool:
    if not users.is_allowed(uid):
        log.warning("daily skipped: uid=%s is not allowed", uid)
        return False
    userctx.set_user(uid)
    async with session.lock_for(uid):
        if not vault.daily_question_due(
            DAILY_TZ,
            DAILY_INTERVAL_MIN_DAYS,
            DAILY_INTERVAL_MAX_DAYS,
        ):
            log.info("scheduled question skipped: interval not elapsed uid=%s", uid)
            return False
        q_num = await _send_next_question(bot, uid)
        if q_num is None:
            return False
        current = session.get()
        vault.mark_daily_sent_details(
            DAILY_TZ,
            q_num=q_num,
            session_id=current.id if current is not None else None,
            min_days=DAILY_INTERVAL_MIN_DAYS,
            max_days=DAILY_INTERVAL_MAX_DAYS,
        )
        return True
