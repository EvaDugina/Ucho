"""Ежедневный вопрос без мировоззренческого графа."""
from __future__ import annotations

import logging
import random
from dataclasses import dataclass

from aiogram import Bot

from .. import books, mood_file, moods, session, userctx, users, vault
from ..config import ALLOWED_TELEGRAM_IDS, DAILY_TZ, DOMAINS, OWNER_TELEGRAM_ID
from ..errors import LLMError
from ..llm import ask_next
from .session_messages import question_field_with_face, send_question

log = logging.getLogger(__name__)


@dataclass
class DailySendResult:
    sent: bool
    q_num: int | None = None


def daily_targets() -> list[int]:
    targets = set(users.allowed_ids()) | set(users.all_data_user_ids())
    targets.add(OWNER_TELEGRAM_ID)
    targets.update(ALLOWED_TELEGRAM_IDS)
    return sorted(targets)


async def _send_next_question(bot: Bot, chat_id: int, domain: str | None = None) -> int | None:
    s = session.get() or session.start(mode="probe")
    selected_domain = domain if domain in DOMAINS else random.choice(tuple(DOMAINS))
    try:
        await bot.send_chat_action(chat_id, "typing")
    except Exception:
        pass

    bot_mood = None
    try:
        mood_vector = moods.session_mood(
            getattr(s, "mood_trajectory", []) or [],
            mood_file.baseline(),
        )
        bot_mood = moods.pick_bot_mood(mood_vector)
    except Exception:
        log.exception("daily mood pick failed (non-fatal)")

    try:
        result = await ask_next(
            domain=selected_domain,
            recent_raw="",
            hint=None,
            bot_mood=bot_mood,
            mode=s.mode,
        )
    except LLMError:
        log.warning("daily ask_next LLM error; user reply suppressed")
        return None

    q_num = vault.next_q_num()
    question = str(result["question"])
    session.set_question(
        question_field_with_face(question, bot_mood),
        domain=selected_domain,
        q_num=q_num,
        metadata={"source": "daily", "topic": selected_domain},
    )
    s.main_question = question
    s.main_q_num = q_num
    session.persist()
    await send_question(
        bot,
        chat_id,
        q_num=q_num,
        mode=s.mode,
        domain=selected_domain,
        text=question,
        bot_mood=bot_mood,
        metadata={"source": "daily", "topic": selected_domain},
    )
    books.expire_pending_reminder()
    return q_num


async def send_daily_question(bot: Bot, uid: int) -> bool:
    userctx.set_user(uid)
    if vault.daily_already_sent(DAILY_TZ):
        log.info("daily skipped: already sent today uid=%s", uid)
        return False
    session.start(mode="probe")
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
