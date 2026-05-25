"""Стартовая оркестрация: дожатие прерванного ответа и слив офлайн-бэклога.

Вынесено из ``handlers.py`` — это НЕ транспорт (не aiogram-роутинг), а логика,
которую дёргает ``main.py`` при старте: восстановить незавершённый LLM-цикл и
обработать сообщения, пришедшие пока контейнер лежал. Завязана на приватные
хелперы хэндлеров (отправка реплики, probe-цикл, приём текста) — поэтому
импортирует ``handlers`` и зовёт их через него; обратной зависимости нет.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from aiogram import Bot, Dispatcher
from aiogram.types import Message

from . import handlers, inbox, ratelimit, session, userctx, users, vault
from .config import DOMAINS
from .errors import LLMError
from .llm import process_answer
from .services.answer_service import apply_processed

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class InboxRecovery:
    uid: int
    action: Literal["pending", "notify_saved"]
    q_num: int | None
    message_id: int | None


def _parse_dt(value: object | None) -> datetime | None:
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str) and value:
        try:
            dt = datetime.fromisoformat(value)
        except ValueError:
            return None
    else:
        return None
    if dt.tzinfo is not None:
        return dt.astimezone().replace(tzinfo=None)
    return dt


def _has_bot_message_after(s: session.Session, entry: dict) -> bool:
    """Был ли уже ответ бота после inbox-сообщения."""
    mid = entry.get("message_id")
    if mid is not None:
        try:
            user_mid = int(mid)
            if any(int(bot_mid) > user_mid for bot_mid in (s.message_ids or [])):
                return True
        except (TypeError, ValueError):
            pass

    user_ts = _parse_dt(entry.get("ts"))
    if user_ts is None:
        return False
    for h in s.history or []:
        if h.get("role") != "assistant":
            continue
        assistant_ts = _parse_dt(h.get("ts") or h.get("timestamp"))
        if assistant_ts is not None and assistant_ts > user_ts:
            return True
    return False


def mark_unanswered_inbox_as_pending(restored: list[tuple[int, session.Session]]) -> list[InboxRecovery]:
    """Найти входящие user-ответы без bot-ответа и подготовить recovery.

    Сценарий: Telegram update уже дошёл до middleware и попал в durable inbox,
    но процесс умер до `_handle_probe_locked`/`pending_answer` или до отправки
    реакции. На старте восстанавливаем такой ход, если сессия ещё открыта.
    """
    recoveries: list[InboxRecovery] = []
    for uid, s in restored:
        userctx.set_user(uid)
        if s.mode != "probe" or not s.last_question or s.pending_answer:
            continue
        entry = inbox.latest_text_for_session(s.id)
        if entry is None or _has_bot_message_after(s, entry):
            continue
        text = (entry.get("text") or "").strip()
        if not text:
            continue
        q_num = entry.get("q_num") if isinstance(entry.get("q_num"), int) else s.current_q_num
        message_id = entry.get("message_id") if isinstance(entry.get("message_id"), int) else None
        if q_num is not None and vault.find_question(int(q_num)) is not None:
            vault.append_log(
                "warn",
                "inbox_unanswered_already_saved",
                f"Q{q_num} mid={message_id} len={len(text)}",
            )
            recoveries.append(InboxRecovery(uid, "notify_saved", int(q_num), message_id))
            continue
        s.pending_answer = text
        session.persist()
        vault.append_log(
            "warn",
            "inbox_unanswered_recovered",
            f"Q{q_num} mid={message_id} len={len(text)}",
        )
        log.warning("recovered unanswered inbox message: uid=%s q=%s mid=%s", uid, q_num, message_id)
        recoveries.append(InboxRecovery(uid, "pending", int(q_num) if q_num is not None else None, message_id))
    return recoveries


async def notify_saved_without_reply(bot: Bot, rec: InboxRecovery) -> None:
    """Ответить, если user-ответ уже попал в raw, но bot-реплика не ушла."""
    userctx.set_user(rec.uid)
    s = session.get()
    if s is None:
        return
    q_label = f"Q{rec.q_num}" if rec.q_num is not None else "прошлый ответ"
    text = (
        f"Я вижу твой ответ на {q_label}: он уже сохранён, но моя реплика "
        "сорвалась при рестарте. Продолжим отсюда."
    )
    new_n = vault.next_q_num()
    next_domain = s.last_domain if s.last_domain in DOMAINS else "everyday"
    session.set_question(text, next_domain, q_num=new_n)
    session.persist()
    try:
        await handlers._send_question(
            bot,
            rec.uid,
            q_num=new_n,
            mode=s.mode,
            domain=next_domain,
            text=text,
            plain=True,
        )
    except Exception:
        log.exception("recovery: failed to notify saved unanswered inbox")


async def process_pending_on_startup(bot: Bot, uid: int) -> None:
    """Дожать висящий ответ конкретного пользователя после рестарта (recovery).

    Вызывается из main.py для каждого пользователя с непустым pending_answer.
    Выставляет userctx на uid, все ответы шлёт в его личный чат (chat_id == uid).
    """
    userctx.set_user(uid)
    s = session.get()
    if s is None or not s.pending_answer:
        return
    if s.mode == "review":
        # Recovery review-сценария не делаем — сбрасываем pending тихо.
        vault.append_log("warn", "pending_answer_review_dropped", f"len={len(s.pending_answer)}")
        s.pending_answer = None
        session.persist()
        return

    text = s.pending_answer
    q_num = s.current_q_num or vault.next_q_num()
    question = s.last_question or s.main_question or ""

    vault.append_log(
        "warn",
        "pending_answer_recovered",
        f"Q{q_num} mode={s.mode} len={len(text)}",
    )

    try:
        await bot.send_message(
            uid,
            f"Дожимаю твой ответ на Q{q_num} — обработка прервалась при рестарте.",
        )
        await bot.send_chat_action(uid, "typing")
    except Exception:
        log.exception("recovery: failed to notify owner")

    real_hint = handlers._real_domain(s.last_domain) or handlers._real_domain(s.domain)
    context_concepts = handlers._context_for_domain(real_hint)
    if not s.history or s.history[-1].get("role") != "user" or s.history[-1].get("content") != text:
        s.record_user(text)
    session_context = s.render_transcript()

    try:
        result = await process_answer(
            question=question,
            answer=text,
            domain_hint=real_hint,
            context_concepts=context_concepts,
            session_context=session_context,
            mode=s.mode,
        )
    except LLMError as exc:
        log.warning("recovery: process_answer LLM error")
        vault.append_log("warn", "pending_answer_recovery_llm_unavailable", f"Q{q_num} process_answer LLMError")
        try:
            await bot.send_message(
                uid,
                f"{getattr(exc, 'user_message', 'Модели OpenRouter сейчас недоступны. Попробуй позже.')} "
                "Pending-ответ оставлен на следующий рестарт. Если не хочешь ждать — /start закроет сессию.",
            )
        except Exception:
            pass
        return  # pending_answer сохранён — повторим в следующий раз.
    except Exception:
        log.exception("recovery: process_answer failed")
        vault.append_log("error", "pending_answer_recovery_failed", f"Q{q_num} process_answer raised")
        try:
            await bot.send_message(
                uid,
                "Не вышло прогнать через LLM. Pending-ответ оставлен на следующий рестарт. "
                "Если не хочешь ждать — /start закроет сессию.",
            )
        except Exception:
            pass
        return  # pending_answer сохранён — повторим в следующий раз.

    try:
        apply_processed(result, q_num, s.asked_at, question, text, session_domain=real_hint)
    except Exception:
        log.exception("recovery: apply_processed failed")
        vault.append_log("error", "pending_answer_apply_failed", f"Q{q_num} apply_processed raised")

    # Графа коснулись (даже частично) — снимаем pending, чтоб не задвоить.
    s.pending_answer = None
    session.persist()

    # Реакция (как в _handle_probe): реплика-укол, сессия остаётся открытой.
    reaction = (result.get("reaction") or "").strip() or "Складно. Слишком складно."
    new_n = vault.next_q_num()
    next_domain = s.last_domain if s.last_domain in DOMAINS else "everyday"
    session.set_question(reaction, next_domain, q_num=new_n)
    session.persist()
    try:
        await handlers._send_question(
            bot, uid,
            q_num=new_n, mode=s.mode, domain=next_domain, text=reaction, plain=True,
        )
    except Exception:
        log.exception("recovery: failed to send reaction")


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
    try:
        await bot.send_message(
            uid, f"Пока меня не было, ты прислал {len(msgs)} сообщ. — отвечаю разом."
        )
    except Exception:
        log.exception("offline backlog: notify failed for uid=%s", uid)

    s = session.get()
    if s is not None and s.mode == "probe" and s.last_question:
        if not ratelimit.try_acquire(uid):
            return
        try:
            # Склеенный ответ как один ход → одна реакция; сессия остаётся открытой.
            await handlers._handle_probe(carrier, combined)
            vault.commit_all("offline batch")
        finally:
            ratelimit.release(uid)
    else:
        # Нет активной probe-сессии — склеить в заметку (тоже один итог).
        clean = await handlers._accept_user_text(carrier, combined)
        if clean is not None:
            await handlers._ingest_note(
                carrier, clean,
                note_prefix="Пока меня не было — склеил сообщения в заметку",
            )
