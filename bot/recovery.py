"""Recovery незавершённых raw-first ответов и офлайн-бэклога."""
from __future__ import annotations

import logging

from aiogram import Bot, Dispatcher
from aiogram.types import Message

from . import about, mood_file, moods, ratelimit, session, session_log, userctx, users, vault
from .errors import LLMError
from .llm import classify_mood, process_answer
from .services import conversation_service, note_service, session_messages
from .services.answer_service import apply_processed
from .validation import MAX_USER_TEXT, safe_user_text

log = logging.getLogger(__name__)


async def process_pending_on_startup(bot: Bot, uid: int) -> None:
    """Дожать текст, который уже попал в session-log до сбоя LLM."""
    if not users.is_allowed(uid):
        log.info("pending recovery skipped: uid=%s is not allowed", uid)
        return
    userctx.set_user(uid)
    current = session.get()
    if current is None or not session.has_pending(current):
        return
    text = session.pending_answer_text(current)
    raw_event_id = current.pending_answer_event_id
    if not text or not raw_event_id:
        current.pending_answer = None
        current.pending_answer_event_id = None
        session.persist()
        return
    event = session_log.find_event(raw_event_id)
    if event is None:
        log.error("pending raw event missing uid=%s event=%s", uid, raw_event_id)
        return
    question = current.last_question or current.main_question or ""
    domain = conversation_service.real_domain(current.last_domain) or "everyday"
    q_num = int(event.get("q_num") or current.current_q_num or vault.next_q_num())
    transcript = current.render_transcript()
    mood_vector = None
    try:
        classified = await classify_mood(
            text,
            about.render_for_prompt(),
            session_context=transcript,
        )
        current.record_mood(classified)
        mood_vector = moods.session_mood(current.mood_trajectory, mood_file.baseline())
        mood_file.set_current(mood_vector)
        moods.log_turn(
            mood_vector,
            raw_event_id=raw_event_id,
            session_id=current.id,
            q_num=q_num,
            at=event.get("ts"),
        )
        vault.commit_all("mood recovery")
        result = await process_answer(
            question=question,
            answer=text,
            domain_hint=domain,
            session_context=transcript,
            metadata=event.get("metadata") if isinstance(event.get("metadata"), dict) else None,
        )
        apply_processed(
            result,
            raw_event_id=raw_event_id,
            original_answer=text,
            at=event.get("ts"),
        )
    except Exception:
        log.exception("pending recovery failed uid=%s event=%s", uid, raw_event_id)
        return

    current.pending_answer = None
    current.pending_answer_event_id = None
    reaction = str(result.get("reaction") or "").strip() or "Сообщение принято."
    reaction_q_num = vault.next_q_num()
    session.set_question(reaction, domain, q_num=reaction_q_num)
    session.persist()
    try:
        await session_messages.send_question(
            bot,
            uid,
            q_num=reaction_q_num,
            domain=domain,
            text=reaction,
            plain=True,
            reply_to_message_id=event.get("telegram_message_id"),
        )
        vault.commit_all("recovered answer")
    except Exception:
        log.exception("failed to send recovered reaction uid=%s", uid)


async def process_queued_on_startup(bot: Bot, uid: int) -> None:
    """Дожать durable merge-slot после pending recovery."""
    if not users.is_allowed(uid):
        log.info("queued recovery skipped: uid=%s is not allowed", uid)
        return
    userctx.set_user(uid)
    current = session.get()
    if current is None:
        return
    while session.has_queued(current):
        item = session.pop_queued_answer()
        if not isinstance(item, dict):
            return
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        fragments = item.get("fragments")
        last = fragments[-1] if isinstance(fragments, list) and fragments else {}
        try:
            payload = await conversation_service.process_probe_answer(
                text,
                message_id=last.get("message_id"),
                at=last.get("at"),
                reply_to_message_id=last.get("reply_to_message_id"),
                question=str(item.get("question") or ""),
                domain_hint=item.get("domain"),
                q_num=item.get("origin_q_num") or vault.next_q_num(),
                session_context_snapshot=str(item.get("session_context") or ""),
                event_kind=str(item.get("source") or "answer"),
            )
        except LLMError:
            log.warning("queued recovery LLM unavailable uid=%s", uid)
            return
        if payload is not None:
            await _send_payload(bot, uid, payload)
            vault.commit_all("queued answer")


async def _send_payload(
    bot: Bot,
    uid: int,
    payload: conversation_service.ReactionPayload,
) -> None:
    await session_messages.send_question(
        bot,
        uid,
        q_num=payload.q_num,
        domain=payload.domain,
        text=payload.text,
        plain=True,
        reply_to_message_id=payload.reply_to_user_message_id,
    )


async def process_offline_backlog(bot: Bot, dispatcher: Dispatcher) -> None:
    """Склеить офлайн-тексты пользователя, остальные updates вернуть dispatcher."""
    drained: list = []
    offset: int | None = None
    try:
        while True:
            batch = await bot.get_updates(offset=offset, timeout=0, limit=100)
            if not batch:
                break
            drained.extend(batch)
            offset = batch[-1].update_id + 1
    except Exception:
        log.exception("offline backlog get_updates failed")
        return
    if not drained:
        return

    grouped: dict[int, list[Message]] = {}
    other: list = []
    for update in drained:
        message = getattr(update, "message", None)
        text = (message.text or "").strip() if message is not None and message.text else ""
        uid = message.from_user.id if message is not None and message.from_user else None
        if text and not text.startswith("/") and uid is not None and users.is_allowed(uid):
            grouped.setdefault(uid, []).append(message.as_(bot))
        else:
            other.append(update)
    for uid, messages in grouped.items():
        try:
            await _process_offline_user(bot, uid, messages)
        except Exception:
            log.exception("offline backlog failed uid=%s", uid)
    for update in other:
        try:
            await dispatcher.feed_update(bot, update)
        except Exception:
            log.exception("offline update replay failed")


async def _process_offline_user(bot: Bot, uid: int, messages: list[Message]) -> None:
    if not users.is_allowed(uid):
        return
    userctx.set_user(uid)
    vault.ensure_layout()
    combined = "\n\n".join(
        (message.text or "").strip() for message in messages if message.text
    ).strip()
    clean, truncated = safe_user_text(combined)
    if not clean:
        return
    if truncated:
        vault.append_log(
            "warn",
            "offline_text_truncated",
            f"len={len(combined)}>{MAX_USER_TEXT}",
        )
    carrier = messages[-1]
    if not ratelimit.try_acquire(uid):
        return
    try:
        current = session.get()
        if current is not None and current.last_question:
            payload = await conversation_service.process_probe_answer(
                clean,
                message_id=carrier.message_id,
                at=carrier.date,
                reply_to_message_id=(
                    carrier.reply_to_message.message_id if carrier.reply_to_message else None
                ),
            )
        else:
            session.start(domain="everyday")
            payload = await note_service.ingest_note(
                clean,
                at=carrier.date,
                message_id=carrier.message_id,
                metadata={"source": "offline"},
            )
        if payload is not None:
            await _send_payload(bot, uid, payload)
            vault.commit_all("offline batch")
    except LLMError:
        log.warning("offline analysis unavailable uid=%s", uid)
    finally:
        ratelimit.release(uid)
