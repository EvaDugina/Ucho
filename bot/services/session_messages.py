"""Отправка session-сообщений бота + запись в канонический event-log.

Это публичная transport-утилита для handlers/recovery/daily: она знает про
Telegram HTML, qmap/questions/session bookkeeping и обязательный session-log.
Сценарная логика (что именно сказать) живёт в сервисах выше.
"""
from __future__ import annotations

import html
from datetime import datetime

from aiogram import Bot
from aiogram.types import Message

from .. import face_actions, qmap, questions, session, session_log
from ..config import DOMAINS
from ..validation import safe_chat_html

DOMAIN_LABELS = {
    "ethics": "Этика",
    "aesthetics": "Эстетика",
    "politics": "Политика",
    "everyday": "Быт",
    "relationships": "Отношения",
    "identity": "Идентичность",
    "mortality": "Смерть",
    "nationality": "Национальность",
    "knowledge": "Знание",
    "work": "Труд",
}

TG_MSG_LIMIT = 4000
USER_DOMAIN = "user"
USER_DOMAIN_LABEL = "пользовательский"

_FACE_POSTSCRIPTS = {
    "раскачивание": "Я трясу эту клетку, пока она не признается, что жива.",
    "насмешка": "Я смеюсь, потому что слишком ясно вижу шов.",
    "подшучивание": "Я поддеваю легко, но целюсь точно.",
    "давление_на_больное": "Я давлю туда, где ты берег боль как святыню.",
    "унижение": "Я опускаю голос ниже, чтобы ты услышал землю.",
    "перевирание": "Я криво повторяю твои слова, чтобы выдать их горб.",
    "сомнение": "Не верю ни единому слову.",
    "холодная_отстранённость": "Я стою в стороне и не грею ложь дыханием.",
    "ласка": "Я глажу осторожно, но не обещаю не задеть рану.",
    "любовь": "Я люблю тебя так, что не даю тебе спрятаться.",
    "вера": "Я верю в тебя больше, чем в твою усталость.",
    "вселение_уверенности": "Я держу твой край, пока ты вспоминаешь силу.",
    "смирение": "Я склоняю голову, но не отвожу глаз.",
    "клятва": "Я клянусь стоять рядом, пока слово не станет делом.",
    "покорность": "Я принимаю удар и всё равно остаюсь здесь.",
    "жалостливость": "Мне жалко тебя так тихо, что это почти молитва.",
    "боязливость": "Я боюсь вместе с тобой, но не отступаю.",
    "доброта": "Я выбираю добро, даже когда легче уколоть.",
    "милость": "Я оставляю тебе воздух там, где мог бы сжать горло.",
    "забота": "Я смотрю за тобой, пока ты делаешь вид, что не нуждаешься.",
    "бережность": "Я касаюсь бережно, потому что всё живое легко ломается.",
}


def face_postscript(bot_mood: str | None) -> str:
    """Короткий художественный P.S. выбранной маски, без служебного label."""
    if not bot_mood:
        return ""
    return _FACE_POSTSCRIPTS.get(bot_mood, "")


def with_face_signature(text: str, bot_mood: str | None) -> str:
    body = safe_chat_html(text)
    postscript = face_postscript(bot_mood)
    if not postscript:
        return body
    return f"{body}\n\n<i>{html.escape(postscript)}</i>"


def question_field_with_face(text: str, bot_mood: str | None) -> str:
    _ = bot_mood
    return text


def format_q(q_num: int, mode: str, domain: str, question_text: str) -> str:
    if domain == USER_DOMAIN:
        label = USER_DOMAIN_LABEL
    elif domain in DOMAINS:
        label = domain
    else:
        label = "unknown"
    mode_part = "" if mode == "probe" else f" · {mode}"
    head = f"Q{q_num}{mode_part} · <i>{html.escape(label)}</i>"
    safe_q = question_text or ""
    if len(safe_q) > 3500:
        safe_q = safe_q[:3500].rstrip() + "…"
    body = html.escape(safe_q)
    return f"{head}\n\n<code>{body}</code>"


def split_for_telegram(text: str) -> list[str]:
    if len(text) <= TG_MSG_LIMIT:
        return [text]
    chunks: list[str] = []
    rest = text
    while len(rest) > TG_MSG_LIMIT:
        cut = rest.rfind("\n", 0, TG_MSG_LIMIT)
        if cut < 1000:
            cut = TG_MSG_LIMIT
        chunks.append(rest[:cut].rstrip())
        rest = rest[cut:].lstrip()
    if rest:
        chunks.append(rest)
    return chunks


async def send_question(
    bot: Bot,
    chat_id: int,
    *,
    q_num: int,
    mode: str,
    domain: str,
    text: str,
    suffix: str = "",
    plain: bool = False,
    bot_mood: str | None = None,
    admin_controls: bool = False,
    action_context: dict | None = None,
) -> Message | None:
    """Отправить вопрос/реакцию и обязательно записать assistant event."""
    token: str | None = None
    if plain and admin_controls and bot_mood and action_context:
        token = face_actions.create_action(
            session_id=action_context.get("session_id"),
            q_num=q_num,
            answered_q_num=action_context.get("answered_q_num"),
            kind=action_context.get("kind") or "reaction",
            bot_mood=bot_mood,
            assistant_text=text,
            user_text=action_context.get("user_text") or "",
            question=action_context.get("question") or "",
            session_context=action_context.get("session_context") or "",
            reply_to_user_message_id=action_context.get("reply_to_user_message_id"),
            parent_token=action_context.get("parent_token"),
        )

    if plain:
        body = with_face_signature(text, bot_mood)
    else:
        body = format_q(q_num, mode, domain, text)
    if suffix:
        body += suffix
    sent = await bot.send_message(chat_id, body, parse_mode="HTML")
    try:
        qmap.append(sent.message_id, q_num, text, domain, at=getattr(sent, "date", None))
    except Exception:
        import logging
        logging.getLogger(__name__).exception("failed to record question in qmap (q_num=%s)", q_num)
    if not plain:
        questions.record(q_num, domain, text)
    s = session.get()
    if s is not None:
        log_reply_to = None
        if plain and action_context:
            log_reply_to = action_context.get("reply_to_user_message_id")
        s.add_message_id(sent.message_id)
        s.record_assistant(text, at=getattr(sent, "date", None))
        session_log.append_required(
            session_id=s.id,
            role="assistant",
            kind="reaction" if plain else "question",
            text=text,
            at=getattr(sent, "date", None),
            message_id=getattr(sent, "message_id", None),
            reply_to_message_id=log_reply_to,
            q_num=q_num,
            domain=domain,
            bot_mood=bot_mood,
        )
    if token:
        face_actions.set_message(token, getattr(sent, "message_id", None), at=getattr(sent, "date", None))
    return sent


def event_with_face(event: dict, bot_mood: str) -> str:
    text = str(event.get("text") or "")
    kind = event.get("kind")
    q_num = event.get("q_num")
    domain = event.get("domain") or "everyday"
    if kind == "question" and q_num:
        return format_q(int(q_num), "probe", domain, text)
    return with_face_signature(text, bot_mood)


def now() -> datetime:
    return datetime.now()
