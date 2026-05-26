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


def with_face_signature(text: str, bot_mood: str | None) -> str:
    body = safe_chat_html(text)
    if bot_mood:
        body += f"\n\n<i>лицо Иуды: {html.escape(bot_mood)}</i>"
    return body


def question_field_with_face(text: str, bot_mood: str | None) -> str:
    if not bot_mood:
        return text
    marker = f"лицо Иуды: {bot_mood}"
    if marker in text:
        return text
    return f"{text}\n\n{marker}"


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
    reply_markup = None
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
        from ..handlers import _face_keyboard  # local import avoids module cycle
        reply_markup = _face_keyboard(token)

    if plain:
        body = with_face_signature(text, bot_mood) if token else safe_chat_html(text)
    else:
        body = format_q(q_num, mode, domain, text)
        if bot_mood:
            body += f"\n\n<i>лицо Иуды: {html.escape(bot_mood)}</i>"
    if suffix:
        body += suffix
    sent = await bot.send_message(chat_id, body, parse_mode="HTML", reply_markup=reply_markup)
    try:
        qmap.append(sent.message_id, q_num, text, domain, at=getattr(sent, "date", None))
    except Exception:
        import logging
        logging.getLogger(__name__).exception("failed to record question in qmap (q_num=%s)", q_num)
    if not plain:
        questions.record(q_num, domain, text)
    s = session.get()
    if s is not None:
        s.add_message_id(sent.message_id)
        s.record_assistant(text, at=getattr(sent, "date", None))
        session_log.append_required(
            session_id=s.id,
            role="assistant",
            kind="reaction" if plain else "question",
            text=text,
            at=getattr(sent, "date", None),
            message_id=getattr(sent, "message_id", None),
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
        body = format_q(int(q_num), "probe", domain, text)
        body += f"\n\n<i>лицо Иуды: {html.escape(bot_mood)}</i>"
        return body
    return with_face_signature(text, bot_mood)


def now() -> datetime:
    return datetime.now()
