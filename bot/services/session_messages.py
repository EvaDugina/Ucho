"""Единая отправка session-сообщений с обязательной raw-записью."""
from __future__ import annotations

import html

from aiogram import Bot
from aiogram.types import Message

from .. import session, session_log
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

def format_q(q_num: int, domain: str = "", question_text: str = "") -> str:
    label = DOMAIN_LABELS.get(domain, domain or "на выбор")
    body = question_text[:3500].rstrip() + ("…" if len(question_text) > 3500 else "")
    return f"Q{q_num} · <i>{html.escape(label)}</i>\n\n<code>{html.escape(body)}</code>"


def split_for_telegram(text: str) -> list[str]:
    if len(text) <= TG_MSG_LIMIT:
        return [text]
    chunks: list[str] = []
    rest = text
    while len(rest) > TG_MSG_LIMIT:
        cut = rest.rfind("\n", 0, TG_MSG_LIMIT)
        cut = cut if cut >= 1000 else TG_MSG_LIMIT
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
    domain: str = "",
    text: str = "",
    suffix: str = "",
    plain: bool = False,
    reply_to_message_id: int | None = None,
    event_kind: str | None = None,
    metadata: dict | None = None,
) -> Message:
    body = safe_chat_html(text) if plain else format_q(q_num, domain, text)
    if suffix:
        body += suffix
    sent = await bot.send_message(chat_id, body, parse_mode="HTML")
    current = session.get()
    if current is not None:
        current.add_message_id(sent.message_id)
        session_log.append_required(
            session_id=current.id,
            role="assistant",
            kind=event_kind or ("reaction" if plain else "question"),
            text=text,
            at=getattr(sent, "date", None),
            message_id=sent.message_id,
            reply_to_message_id=reply_to_message_id if plain else None,
            q_num=q_num,
            domain=domain,
            metadata=metadata,
        )
    return sent


async def send_plain_session_message(
    bot: Bot,
    chat_id: int,
    *,
    session_id: str,
    q_num: int,
    domain: str = "",
    text: str,
    event_kind: str,
    metadata: dict | None = None,
) -> Message:
    """Отправить plain-реплику и обязательно записать её в указанную сессию."""
    sent = await bot.send_message(chat_id, safe_chat_html(text), parse_mode="HTML")
    session_log.append_required(
        session_id=session_id,
        role="assistant",
        kind=event_kind,
        text=text,
        at=getattr(sent, "date", None),
        message_id=sent.message_id,
        q_num=q_num,
        domain=domain,
        metadata=metadata,
    )
    return sent
