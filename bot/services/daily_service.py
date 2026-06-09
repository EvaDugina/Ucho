"""Daily-question use case without dependency on handlers internals."""
from __future__ import annotations

import logging
from dataclasses import dataclass

from aiogram import Bot

from .. import mood_file, moods, session, userctx, users, vault
from ..config import ALLOWED_TELEGRAM_IDS, DAILY_TZ, OWNER_TELEGRAM_ID
from ..errors import LLMError
from ..llm import ask_next
from ..worldview_taxonomy import choose_random_target, coerce_target
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


async def _send_next_question(bot: Bot, chat_id: int, target: dict | None = None) -> int | None:
    s = session.get()
    if s is None:
        s = session.start(mode="probe", target=target)
    target = coerce_target(
        (target or {}).get("area"),
        (target or {}).get("category"),
        (target or {}).get("theme"),
    ) if target else choose_random_target()
    log.info("worldview target selected for daily question: %s", target["theme_key"])
    try:
        await bot.send_chat_action(chat_id, "typing")
    except Exception:
        pass

    bot_mood = None
    try:
        mv = moods.session_mood(getattr(s, "mood_trajectory", []) or [], mood_file.baseline())
        bot_mood = moods.pick_bot_mood(mv)
    except Exception:
        log.exception("daily mood pick failed (non-fatal)")

    try:
        result = await ask_next(
            target=target,
            context_atoms="",
            recent_raw="",
            hint=None,
            bot_mood=bot_mood,
            mode=s.mode,
        )
    except LLMError:
        log.warning("daily ask_next LLM error; user reply suppressed")
        return None
    q_num = vault.next_q_num()
    session.set_question(question_field_with_face(result["question"], bot_mood), target=result, q_num=q_num)
    s.main_question = result["question"]
    s.main_q_num = q_num
    s.clarifier_count = 0
    session.persist()
    await send_question(
        bot, chat_id,
        q_num=q_num, mode=s.mode, area=result["area"], category=result["category"],
        theme=result["theme"], theme_key=result["theme_key"], text=result["question"],
        bot_mood=bot_mood,
    )
    return q_num


async def send_daily_question(bot: Bot, uid: int) -> bool:
    userctx.set_user(uid)
    if vault.daily_already_sent(DAILY_TZ):
        log.info("daily skipped: already sent today uid=%s", uid)
        return False
    session.start(mode="probe")
    q_num = await _send_next_question(bot, uid, target=None)
    if q_num is None:
        return False
    s = session.get()
    vault.mark_daily_sent_details(
        DAILY_TZ,
        q_num=q_num,
        session_id=s.id if s is not None else None,
    )
    vault.commit_all("daily question")
    return True
