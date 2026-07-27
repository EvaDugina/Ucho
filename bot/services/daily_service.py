"""Ежедневный вопрос без мировоззренческого графа."""
from __future__ import annotations

import logging
import random
from contextlib import suppress

from aiogram import Bot

from .. import books, session, userctx, users, vault
from ..config import DAILY_TZ, DOMAINS
from ..errors import LLMError
from ..llm import ask_next
from .session_messages import send_question

log = logging.getLogger(__name__)


def daily_targets() -> list[int]:
    return sorted(users.allowed_ids())


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
        if vault.daily_already_sent(DAILY_TZ):
            log.info("daily skipped: already sent today uid=%s", uid)
            return False
        q_num = await _send_next_question(bot, uid)
        if q_num is None:
            return False
        current = session.get()
        vault.mark_daily_sent_details(
            DAILY_TZ,
            q_num=q_num,
            session_id=current.id if current is not None else None,
        )
        vault.commit_all("daily question")
        return True
