"""Автоматический вопрос раз в несколько дней без мировоззренческого графа."""
from __future__ import annotations

import logging
import random
from contextlib import suppress

from aiogram import Bot

from .. import books, session, session_log, userctx, users, vault
from ..config import DAILY_INTERVAL_DAYS, DAILY_TZ, DOMAINS
from ..errors import LLMError
from ..llm import UNANSWERED_MOTIFS, ask_next, generate_unanswered_followup
from .session_messages import send_question

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
    current_q_num: int,
    current_domain: str,
    previous: dict,
    last_answered_session: str,
) -> bool:
    motif = random.choice(UNANSWERED_MOTIFS)
    try:
        text = await generate_unanswered_followup(
            unanswered_question=str(previous["text"]),
            last_answered_session=last_answered_session,
            motif=motif,
        )
        await send_question(
            bot,
            chat_id,
            q_num=current_q_num,
            domain=current_domain,
            text=text,
            plain=True,
            event_kind="unanswered_followup",
            metadata={
                "source": "scheduled_unanswered_followup",
                "motif": motif,
                "unanswered_q_num": previous.get("q_num"),
                "unanswered_session_id": previous.get("session_id"),
            },
        )
        return True
    except Exception:
        # Новый вопрос уже доставлен и записан: сбой дополнительной реплики не
        # должен дублировать его при следующем тике расписания.
        log.exception("scheduled unanswered followup failed uid=%s", chat_id)
        return False


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
        if not vault.daily_question_due(DAILY_TZ, DAILY_INTERVAL_DAYS):
            log.info("scheduled question skipped: interval not elapsed uid=%s", uid)
            return False
        previous = _previous_unanswered_daily()
        last_answered_session = (
            session_log.latest_answered_session_transcript() if previous is not None else ""
        )
        q_num = await _send_next_question(bot, uid)
        if q_num is None:
            return False
        current = session.get()
        vault.mark_daily_sent_details(
            DAILY_TZ,
            q_num=q_num,
            session_id=current.id if current is not None else None,
        )
        if previous is not None and current is not None:
            await _send_unanswered_followup(
                bot,
                uid,
                current_q_num=q_num,
                current_domain=current.last_domain,
                previous=previous,
                last_answered_session=last_answered_session,
            )
        return True
