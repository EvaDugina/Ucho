"""Стартовая оркестрация: дожатие прерванного ответа и слив офлайн-бэклога.

Вынесено из ``handlers.py`` — это НЕ транспорт (не aiogram-роутинг), а логика,
которую дёргает ``main.py`` при старте: восстановить незавершённый LLM-цикл и
обработать сообщения, пришедшие пока контейнер лежал.
"""
from __future__ import annotations

import logging
from aiogram import Bot, Dispatcher
from aiogram.types import Message

from . import moods, ratelimit, session, session_log, userctx, users, vault
from .config import DOMAINS
from .errors import LLMError
from .llm import process_answer
from .services import conversation_service, note_service, session_messages
from .services.answer_service import apply_processed
from .validation import MAX_USER_TEXT, safe_user_text

log = logging.getLogger(__name__)


async def process_pending_on_startup(bot: Bot, uid: int) -> None:
    """Дожать висящий ответ конкретного пользователя после рестарта (recovery).

    Вызывается из main.py для каждого пользователя с непустым pending_answer.
    Выставляет userctx на uid, все ответы шлёт в его личный чат (chat_id == uid).
    """
    userctx.set_user(uid)
    s = session.get()
    if s is None or not session.has_pending(s):
        return
    if s.mode == "review":
        # Recovery review-сценария не делаем — сбрасываем pending тихо.
        text = session.pending_answer_text(s) or ""
        vault.append_log("warn", "pending_answer_review_dropped", f"len={len(text)}")
        s.pending_answer = None
        s.pending_answer_event_id = None
        session.persist()
        return

    text = session.pending_answer_text(s)
    if not text:
        s.pending_answer = None
        s.pending_answer_event_id = None
        session.persist()
        return
    q_num = s.current_q_num or vault.next_q_num()
    question = s.last_question or s.main_question or ""
    pending_event_id = s.pending_answer_event_id

    vault.append_log(
        "warn",
        "pending_answer_recovered",
        f"Q{q_num} mode={s.mode} len={len(text)}",
    )

    try:
        await bot.send_chat_action(uid, "typing")
    except Exception:
        log.exception("recovery: failed to send chat action")

    real_hint = conversation_service.real_domain(s.last_domain) or conversation_service.real_domain(s.domain)
    context_concepts = conversation_service.context_for_domain(real_hint)
    if not s.history or s.history[-1].get("role") != "user" or s.history[-1].get("content") != text:
        s.record_user(text)
    session_context = s.render_transcript()
    bot_mood = moods.random_bot_mood()

    try:
        result = await process_answer(
            question=question,
            answer=text,
            domain_hint=real_hint,
            context_concepts=context_concepts,
            bot_mood=bot_mood,
            session_context=session_context,
            mode=s.mode,
        )
    except LLMError:
        log.warning("recovery: process_answer LLM error; user reply suppressed")
        vault.append_log("warn", "pending_answer_recovery_llm_unavailable", f"Q{q_num} process_answer LLMError")
        return  # pending ref сохранён — повторим в следующий раз.
    except Exception:
        log.exception("recovery: process_answer failed")
        vault.append_log("error", "pending_answer_recovery_failed", f"Q{q_num} process_answer raised")
        return  # pending ref сохранён — повторим в следующий раз.

    moods.record_mask_frequency_draft(result.get("mask_frequency_draft"), bot_mood=bot_mood)

    try:
        apply_processed(result, q_num, s.asked_at, question, text, session_domain=real_hint)
    except Exception:
        log.exception("recovery: apply_processed failed")
        vault.append_log("error", "pending_answer_apply_failed", f"Q{q_num} apply_processed raised")

    # Графа коснулись (даже частично) — снимаем pending, чтоб не задвоить.
    s.pending_answer = None
    s.pending_answer_event_id = None
    session.persist()

    # Реакция (как в _handle_probe): реплика-укол, сессия остаётся открытой.
    reaction = (result.get("reaction") or "").strip() or "Складно. Слишком складно."
    new_n = vault.next_q_num()
    next_domain = s.last_domain if s.last_domain in DOMAINS else "everyday"
    session.set_question(session_messages.question_field_with_face(reaction, bot_mood), next_domain, q_num=new_n)
    session.persist()
    try:
        user_event = session_log.find_event(pending_event_id)
        await session_messages.send_question(
            bot, uid,
            q_num=new_n, mode=s.mode, domain=next_domain, text=reaction, plain=True,
            bot_mood=bot_mood,
            admin_controls=bool(bot_mood),
            action_context={
                "session_id": s.id,
                "answered_q_num": q_num,
                "kind": "reaction",
                "user_text": text,
                "question": question,
                "session_context": session_context,
                "reply_to_user_message_id": (
                    user_event.get("telegram_message_id", user_event.get("message_id"))
                    if user_event else None
                ),
            },
        )
    except Exception:
        log.exception("recovery: failed to send reaction")


async def process_queued_on_startup(bot: Bot, uid: int) -> None:
    """Дожать durable queued_answer после pending recovery и до offline backlog."""
    userctx.set_user(uid)
    s = session.get()
    if s is None or not session.has_queued(s):
        return
    while session.has_queued(s):
        item = session.pop_queued_answer()
        if not isinstance(item, dict):
            return
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        fragments = item.get("fragments")
        last = fragments[-1] if isinstance(fragments, list) and fragments else {}
        try:
            await bot.send_chat_action(uid, "typing")
        except Exception:
            log.exception("queued recovery: failed to send chat action")
        try:
            payload = await conversation_service.process_probe_answer(
                text,
                message_id=last.get("message_id"),
                at=last.get("at"),
                reply_to_message_id=last.get("reply_to_message_id"),
                is_owner=users.is_owner(uid),
                question=str(item.get("question") or ""),
                domain_hint=item.get("domain"),
                q_num=vault.next_q_num(),
                asked_at=item.get("asked_at"),
                session_context_snapshot=str(item.get("session_context") or ""),
                mode=str(item.get("mode") or "probe"),
            )
        except LLMError:
            log.warning("queued recovery: process_answer LLM error; user reply suppressed")
            return
        except Exception:
            log.exception("queued recovery failed")
            return
        if payload is None:
            continue
        if payload.mood_message:
            await bot.send_message(uid, payload.mood_message)
        await session_messages.send_question(
            bot, uid,
            q_num=payload.q_num, mode=payload.mode, domain=payload.domain, text=payload.text, plain=True,
            bot_mood=payload.bot_mood,
            admin_controls=bool(payload.bot_mood),
            action_context={
                "session_id": payload.session_id,
                "answered_q_num": payload.answered_q_num,
                "kind": "reaction",
                "user_text": payload.user_text,
                "question": payload.answered_question,
                "session_context": payload.session_context,
                "reply_to_user_message_id": payload.reply_to_user_message_id,
            },
        )
        vault.commit_all("queued answer")


async def process_offline_backlog(bot: Bot, dp: Dispatcher) -> None:
    """Слить бэклог Telegram ДО старта поллинга и обработать офлайн-сообщения.

    Текстовые сообщения (не команды) от доверенных группируем по uid и склеиваем
    в ОДИН ответ → один итоговый комментарий Иуды (а не ответ на каждое). Команды
    и прочие апдейты переигрываем по одной через dispatcher. Слив через get_updates
    с продвижением offset ack-ает апдейты на сервере — start_polling их не переотдаст.
    """
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
        log.exception("offline backlog: get_updates failed")
        return
    if not drained:
        return

    text_by_uid: dict[int, list[Message]] = {}
    other: list = []
    for u in drained:
        m = getattr(u, "message", None)
        txt = (m.text or "").strip() if (m is not None and m.text) else ""
        uid = m.from_user.id if (m is not None and m.from_user) else None
        if txt and not txt.startswith("/") and uid is not None and users.is_allowed(uid):
            text_by_uid.setdefault(uid, []).append(m.as_(bot))
        else:
            other.append(u)

    log.info(
        "offline backlog: %d updates, %d user(s) with text, %d other",
        len(drained), len(text_by_uid), len(other),
    )

    for uid, msgs in text_by_uid.items():
        try:
            await _process_offline_user(bot, uid, msgs)
        except Exception:
            log.exception("offline backlog failed for uid=%s", uid)

    # Команды / стейл-апдейты — переиграть штатно, в порядке прихода.
    for u in other:
        try:
            await dp.feed_update(bot, u)
        except Exception:
            log.exception("offline backlog: feed_update failed")


async def _process_offline_user(bot: Bot, uid: int, msgs: list[Message]) -> None:
    """Склеить офлайн-сообщения одного пользователя в один ответ."""
    userctx.set_user(uid)
    vault.ensure_layout()
    combined = "\n\n".join((m.text or "").strip() for m in msgs if m.text).strip()
    if not combined:
        return
    carrier = msgs[-1]  # реальный Message (с привязанным ботом) — для answer/typing
    s = session.get()
    if s is not None and s.mode == "probe" and s.last_question:
        if not ratelimit.try_acquire(uid):
            return
        try:
            # Склеенный ответ как один ход → одна реакция; сессия остаётся открытой.
            clean, truncated = safe_user_text(combined)
            if not clean:
                return
            if truncated:
                vault.append_log("warn", "offline_user_text_truncated", f"len(raw)={len(combined)} > {MAX_USER_TEXT}")
            payload = await conversation_service.process_probe_answer(
                clean,
                message_id=getattr(carrier, "message_id", None),
                at=getattr(carrier, "date", None),
                reply_to_message_id=(
                    carrier.reply_to_message.message_id if carrier.reply_to_message is not None else None
                ),
                is_owner=users.is_owner(uid),
            )
            if payload is not None:
                if payload.mood_message:
                    await bot.send_message(uid, payload.mood_message)
                await session_messages.send_question(
                    bot, uid,
                    q_num=payload.q_num,
                    mode=payload.mode,
                    domain=payload.domain,
                    text=payload.text,
                    plain=True,
                    bot_mood=payload.bot_mood,
                    admin_controls=bool(payload.bot_mood),
                    action_context={
                        "session_id": payload.session_id,
                        "answered_q_num": payload.answered_q_num,
                        "kind": "reaction",
                        "user_text": payload.user_text,
                        "question": payload.answered_question,
                        "session_context": payload.session_context,
                        "reply_to_user_message_id": (
                            payload.reply_to_user_message_id or getattr(carrier, "message_id", None)
                        ),
                    },
                )
            vault.commit_all("offline batch")
        except LLMError:
            log.warning("offline backlog: process_answer LLM error; user reply suppressed")
        finally:
            ratelimit.release(uid)
    else:
        # Нет активной probe-сессии — склеить в заметку (тоже один итог).
        clean, truncated = safe_user_text(combined)
        if not clean:
            return
        if truncated:
            vault.append_log("warn", "offline_note_truncated", f"len(raw)={len(combined)} > {MAX_USER_TEXT}")
        if not ratelimit.try_acquire(uid):
            return
        try:
            session.start(mode="probe", domain=None)
            payload = await note_service.ingest_note(clean, at=getattr(carrier, "date", None))
            if payload is not None:
                await session_messages.send_question(
                    bot, uid,
                    q_num=payload.q_num,
                    mode=payload.mode,
                    domain=payload.domain,
                    text=payload.text,
                    plain=True,
                    bot_mood=payload.bot_mood,
                    admin_controls=bool(payload.bot_mood),
                    action_context={
                        "session_id": payload.session_id,
                        "answered_q_num": payload.answered_q_num,
                        "kind": "reaction",
                        "user_text": payload.user_text,
                        "question": payload.answered_question,
                        "session_context": payload.session_context,
                        "reply_to_user_message_id": payload.reply_to_user_message_id,
                    },
                )
        finally:
            ratelimit.release(uid)
