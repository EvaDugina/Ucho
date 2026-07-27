"""Telegram-команды и raw-first диалог Уха.

Здесь намеренно нет графа, доменных профилей и legacy-команд. Любой принятый
пользовательский текст проходит единый путь: session-log → mood → personality.
"""
from __future__ import annotations

import asyncio
import html
import io
import logging

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from . import (
    books,
    face_actions,
    mood_file,
    moods,
    ratelimit,
    session,
    session_log,
    userctx,
    users,
    vault,
)
from .config import BOOK_UPLOAD_MAX_BYTES, DOMAINS, UPLOAD_PENDING_SECONDS
from .errors import LLMError, VaultError
from .llm import ask_book_question, ask_next, regenerate_reaction
from .services import (
    about_service,
    conversation_service,
    deletion_service,
    note_service,
    session_messages,
)
from .validation import MAX_USER_TEXT, safe_chat_html, safe_user_text

log = logging.getLogger(__name__)
router = Router()
admin_router = Router()

LETA_CHAT_PURGE_DELAY_SECONDS = 0.08
SEA_PAGE_SIZE = 8

_DOMAIN_LABELS = session_messages.DOMAIN_LABELS


def _is_owner(message: Message) -> bool:
    return bool(message.from_user and users.is_owner(message.from_user.id))


def _ask_keyboard() -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(text=_DOMAIN_LABELS.get(domain, domain), callback_data=f"ask:{domain}")
        for domain in DOMAINS
    ]
    rows = [buttons[index : index + 2] for index in range(0, len(buttons), 2)]
    rows.append([InlineKeyboardButton(text="Пусть бот выберет сам", callback_data="ask:any")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _remask_keyboard(token: str) -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(text=face, callback_data=f"face:rm:{token}:{index}")
        for index, face in enumerate(moods.BOT_MOODS)
    ]
    return InlineKeyboardMarkup(
        inline_keyboard=[buttons[index : index + 2] for index in range(0, len(buttons), 2)]
    )


def _action_for_message(message_id: int | None) -> dict | None:
    action = face_actions.find_by_message_id(message_id)
    if action is not None:
        return action
    event = session_log.find_event_by_message_id(message_id, role="assistant")
    if not event or event.get("kind") not in {"reaction", "regen"}:
        return None
    token = face_actions.create_action(
        session_id=event.get("session_id"),
        q_num=event.get("q_num"),
        kind=str(event.get("kind")),
        bot_mood=event.get("bot_mood"),
        assistant_text=str(event.get("text") or ""),
        user_text="",
        question="",
        session_context="",
        reply_to_user_message_id=event.get("reply_to_message_id"),
        at=event.get("ts"),
    )
    face_actions.set_message(token, message_id, at=event.get("ts"))
    return face_actions.get_action(token)


def _record_command(message: Message) -> None:
    current = session.get()
    if current is None:
        return
    session_log.append(
        session_id=current.id,
        role="user",
        kind="command",
        text=message.text or message.caption or "",
        at=message.date,
        message_id=message.message_id,
        reply_to_message_id=(
            message.reply_to_message.message_id if message.reply_to_message else None
        ),
        q_num=current.current_q_num,
        domain=current.last_domain,
    )


async def _send_payload(bot: Bot, chat_id: int, payload: conversation_service.ReactionPayload) -> None:
    if payload.mood_message:
        await bot.send_message(chat_id, payload.mood_message)
    await session_messages.send_question(
        bot,
        chat_id,
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


async def _accept_text(message: Message, raw: str) -> str | None:
    clean, truncated = safe_user_text(raw)
    if not clean:
        await message.answer("Пустой ответ. Напиши хоть что-то.")
        return None
    if truncated:
        vault.append_log("warn", "user_text_truncated", f"len={len(raw)}>{MAX_USER_TEXT}")
        await message.answer(f"⚠ Ответ длиннее {MAX_USER_TEXT} символов — хвост обрезан.")
    return clean


async def _process_current_text(
    message: Message,
    clean: str,
    *,
    event_kind: str = "answer",
    metadata: dict | None = None,
) -> None:
    uid = userctx.current_uid()
    if ratelimit.is_inflight(uid):
        session.enqueue_answer(
            clean,
            message_id=message.message_id,
            at=message.date,
            reply_to_message_id=(
                message.reply_to_message.message_id if message.reply_to_message else None
            ),
            source=event_kind,
        )
        await message.answer(ratelimit.BUSY_MESSAGE)
        return
    if not ratelimit.try_acquire(uid):
        await message.answer(ratelimit.BUSY_MESSAGE)
        return
    try:
        async with session.lock_for(uid):
            payload = await conversation_service.process_probe_answer(
                clean,
                message_id=message.message_id,
                at=message.date,
                reply_to_message_id=(
                    message.reply_to_message.message_id if message.reply_to_message else None
                ),
                is_owner=_is_owner(message),
                event_kind=event_kind,
                metadata=metadata,
            )
            if payload is not None:
                await _send_payload(message.bot, message.chat.id, payload)
                vault.commit_all(event_kind)
            await _drain_queued(message)
    except LLMError:
        log.warning("process_answer unavailable; pending raw answer kept")
        await message.answer(
            safe_chat_html(moods.llm_error_fallback_reply()),
            parse_mode="HTML",
        )
    finally:
        ratelimit.release(uid)


async def _drain_queued(message: Message) -> None:
    """Дожать merge-slot в том же single-flight после текущего LLM-хода."""
    while session.has_queued():
        item = session.pop_queued_answer()
        if not isinstance(item, dict):
            return
        fragments = item.get("fragments")
        last = fragments[-1] if isinstance(fragments, list) and fragments else {}
        payload = await conversation_service.process_probe_answer(
            str(item.get("text") or ""),
            message_id=last.get("message_id"),
            at=last.get("at"),
            reply_to_message_id=last.get("reply_to_message_id"),
            is_owner=_is_owner(message),
            question=str(item.get("question") or ""),
            domain_hint=item.get("domain"),
            q_num=item.get("origin_q_num"),
            session_context_snapshot=str(item.get("session_context") or ""),
            mode="probe",
            event_kind=str(last.get("source") or "answer"),
            metadata=item.get("metadata") if isinstance(item.get("metadata"), dict) else None,
        )
        if payload is not None:
            await _send_payload(message.bot, message.chat.id, payload)
            vault.commit_all("queued answer")


async def _ingest_note(message: Message, clean: str, *, source: str = "ucho") -> None:
    session.start(mode="probe", domain="everyday")
    payload = None
    uid = userctx.current_uid()
    if not ratelimit.try_acquire(uid):
        await message.answer(ratelimit.BUSY_MESSAGE)
        return
    try:
        async with session.lock_for(uid):
            payload = await note_service.ingest_note(
                clean,
                at=message.date,
                message_id=message.message_id,
                metadata={"source": source},
            )
            if payload is not None:
                await _send_payload(message.bot, message.chat.id, payload)
                vault.commit_all("note")
    except LLMError:
        log.warning("note analysis unavailable; pending raw note kept")
        await message.answer(
            safe_chat_html(moods.llm_error_fallback_reply()),
            parse_mode="HTML",
        )
    finally:
        ratelimit.release(uid)


async def _generate_question(
    bot: Bot,
    chat_id: int,
    *,
    domain: str | None,
    hint: str | None = None,
) -> None:
    current = session.start(mode="probe", domain=domain)
    try:
        await bot.send_chat_action(chat_id, "typing")
    except Exception:
        pass
    mood_vector = moods.session_mood(current.mood_trajectory, mood_file.baseline())
    bot_mood = moods.pick_bot_mood(mood_vector)
    result = await ask_next(
        domain=domain,
        hint=hint,
        recent_raw="",
        bot_mood=bot_mood,
        mode="probe",
    )
    selected_domain = str(result.get("domain") or domain or "everyday")
    if selected_domain not in DOMAINS:
        selected_domain = "everyday"
    question = str(result["question"]).strip()
    q_num = vault.next_q_num()
    session.set_question(
        question,
        selected_domain,
        q_num=q_num,
        metadata={"source": "ask", "topic": selected_domain},
    )
    current.main_question = question
    current.main_q_num = q_num
    session.persist()
    await session_messages.send_question(
        bot,
        chat_id,
        q_num=q_num,
        mode="probe",
        domain=selected_domain,
        text=question,
        bot_mood=bot_mood,
        metadata={"source": "ask", "topic": selected_domain},
    )
    vault.commit_all("question")


def _sea_keyboard(page: int = 0, *, settings: bool = False) -> tuple[str, InlineKeyboardMarkup]:
    library = books.list_books()
    pages = max(1, (len(library) + SEA_PAGE_SIZE - 1) // SEA_PAGE_SIZE)
    page = min(max(0, page), pages - 1)
    selected = library[page * SEA_PAGE_SIZE : (page + 1) * SEA_PAGE_SIZE]
    rows: list[list[InlineKeyboardButton]] = []
    for book in selected:
        book_id = str(book["id"])
        title = str(book.get("title") or book_id)[:42]
        if settings:
            marker = "✓" if books.reminder_enabled(book_id) else "○"
            rows.append(
                [
                    InlineKeyboardButton(
                        text=f"{marker} {title}",
                        callback_data=f"sea:t:{book_id}:{page}",
                    )
                ]
            )
        else:
            rows.append(
                [InlineKeyboardButton(text=title, callback_data=f"sea:b:{book_id}")]
            )
    navigation: list[InlineKeyboardButton] = []
    prefix = "sea:s" if settings else "sea:p"
    if page > 0:
        navigation.append(InlineKeyboardButton(text="←", callback_data=f"{prefix}:{page - 1}"))
    navigation.append(InlineKeyboardButton(text=f"{page + 1}/{pages}", callback_data="sea:no"))
    if page + 1 < pages:
        navigation.append(InlineKeyboardButton(text="→", callback_data=f"{prefix}:{page + 1}"))
    rows.append(navigation)
    rows.append(
        [
            InlineKeyboardButton(
                text="К списку книг" if settings else "Настройки напоминаний",
                callback_data=f"sea:p:{page}" if settings else f"sea:s:{page}",
            )
        ]
    )
    if not library:
        text = "Библиотека пока пуста. Добавь книгу через /upload."
    elif settings:
        text = "Книжные напоминания. ✓ — книга участвует в выборе цитат."
    else:
        text = "Выбери книгу для разговора:"
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    await message.answer(
        "Я — Ухо. Задаю вопросы, храню raw-разговор, замечаю настроение и черты. "
        "Общая библиотека открывается через /sea. Команды — /help."
    )


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    text = (
        "<b>Разговор</b>\n"
        "/ask — вопрос по одной из десяти тем\n"
        "/ucho &lt;текст&gt; — свободная заметка\n"
        "/about — каким я тебя вижу\n"
        "/pebble — проверить, что я жив\n\n"
        "<b>Книги</b>\n"
        "/upload — загрузить TXT, MD, EPUB или FB2 до 20 МБ\n"
        "/sea — библиотека, разговоры и настройки цитат\n\n"
        "<b>Реплики</b>\n"
        "/like, /regen, /remask — reply-команды для реплики Иуды\n\n"
        "<b>Данные</b>\n"
        "/leta — удалить личный raw, mood, personality и книжные настройки\n"
        "/start, /help — начало и эта справка"
    )
    if _is_owner(message):
        text += "\n\n<b>Админ</b>\n/adduser &lt;id&gt;, /removeuser &lt;id&gt;, /users"
    await message.answer(text, parse_mode="HTML")


@router.message(Command("pebble"))
async def cmd_pebble(message: Message) -> None:
    await message.answer("Больно.")


@router.message(Command("ask"))
async def cmd_ask(message: Message, command: CommandObject) -> None:
    argument = (command.args or "").strip()
    if not argument:
        await message.answer("Выбери тему:", reply_markup=_ask_keyboard())
        return
    domain = argument.lower() if argument.lower() in DOMAINS else None
    hint = None
    if domain is None:
        hint, truncated = safe_user_text(argument, limit=2000)
        if not hint:
            await message.answer("Использование: /ask [тема или затравка]")
            return
        if truncated:
            await message.answer("⚠ Затравка обрезана до 2000 символов.")
    uid = userctx.current_uid()
    if not ratelimit.try_acquire(uid):
        await message.answer(ratelimit.BUSY_MESSAGE)
        return
    try:
        await _generate_question(message.bot, message.chat.id, domain=domain, hint=hint)
    except LLMError:
        log.warning("ask unavailable")
    finally:
        ratelimit.release(uid)


@router.callback_query(F.data.startswith("ask:"))
async def cb_ask_domain(callback: CallbackQuery) -> None:
    value = (callback.data or "").split(":", 1)[1]
    if value != "any" and value not in DOMAINS:
        await callback.answer("Неизвестная тема", show_alert=True)
        return
    await callback.answer()
    uid = userctx.current_uid()
    if not ratelimit.try_acquire(uid):
        await callback.bot.send_message(callback.from_user.id, ratelimit.BUSY_MESSAGE)
        return
    try:
        await _generate_question(
            callback.bot,
            callback.message.chat.id if callback.message else callback.from_user.id,
            domain=None if value == "any" else value,
        )
    except LLMError:
        log.warning("ask callback unavailable")
    finally:
        ratelimit.release(uid)


@router.message(Command("ucho"))
async def cmd_ucho(message: Message, command: CommandObject) -> None:
    clean = await _accept_text(message, command.args or "")
    if clean is not None:
        await _ingest_note(message, clean)


@router.message(Command("about"))
async def cmd_about(message: Message) -> None:
    uid = userctx.current_uid()
    if not ratelimit.try_acquire(uid):
        await message.answer(ratelimit.BUSY_MESSAGE)
        return
    try:
        async with session.lock_for(uid):
            spoken, version = await about_service.refresh_and_present(at=message.date)
            if version:
                log.info("about synthesized uid=%s version=%s", uid, version)
            if not spoken:
                await message.answer(
                    "Я тебя ещё толком не распробовал — поговори со мной через /ask или /ucho."
                )
                return
            for chunk in session_messages.split_for_telegram(safe_chat_html(spoken)):
                await message.answer(chunk, parse_mode="HTML")
    except LLMError:
        log.warning("about unavailable; pending deltas unchanged")
    finally:
        ratelimit.release(uid)


@router.message(Command("upload"))
async def cmd_upload(message: Message) -> None:
    if message.document is not None:
        await _handle_upload_document(message)
        return
    books.begin_upload_wait(seconds=UPLOAD_PENDING_SECONDS)
    await message.answer(
        "Пришли один TXT, MD, EPUB или FB2 до 20 МБ в течение 10 минут."
    )


@router.message(F.document)
async def on_document(message: Message) -> None:
    caption_command = (message.caption or "").split(maxsplit=1)[0].split("@", 1)[0].lower()
    if caption_command != "/upload" and not books.upload_waiting():
        await message.answer("Чтобы добавить книгу, пришли документ с подписью /upload.")
        return
    await _handle_upload_document(message)


async def _handle_upload_document(message: Message) -> None:
    document = message.document
    if document is None:
        return
    if document.file_size and document.file_size > BOOK_UPLOAD_MAX_BYTES:
        books.clear_upload_wait()
        await message.answer("Книга больше 20 МБ.")
        return
    filename = document.file_name or "book.txt"
    buffer = io.BytesIO()
    try:
        await message.bot.download(document, destination=buffer)
        payload = buffer.getvalue()
        result = books.ingest(
            payload,
            filename,
            uploader_uid=int(userctx.current_uid() or 0),
            at=message.date,
        )
    except books.BookError as exc:
        await message.answer(str(exc))
        return
    except Exception:
        log.exception("book upload failed")
        await message.answer("Не удалось принять книгу.")
        return
    finally:
        books.clear_upload_wait()
    if result.get("duplicate"):
        await message.answer(f"Эта книга уже есть: {result.get('title') or result['id']}.")
    else:
        await message.answer(f"Книга добавлена: {result.get('title') or result['id']}.")


@router.message(Command("sea"))
async def cmd_sea(message: Message) -> None:
    text, keyboard = _sea_keyboard()
    await message.answer(text, reply_markup=keyboard)


@router.callback_query(F.data.startswith("sea:"))
async def cb_sea(callback: CallbackQuery) -> None:
    parts = (callback.data or "").split(":")
    action = parts[1] if len(parts) > 1 else ""
    if action == "no":
        await callback.answer()
        return
    if action in {"p", "s"}:
        page = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
        text, keyboard = _sea_keyboard(page, settings=action == "s")
        if callback.message:
            await callback.message.edit_text(text, reply_markup=keyboard)
        await callback.answer()
        return
    if action == "t" and len(parts) >= 4:
        book_id, page_text = parts[2], parts[3]
        books.set_reminder_enabled(book_id, not books.reminder_enabled(book_id))
        page = int(page_text) if page_text.isdigit() else 0
        text, keyboard = _sea_keyboard(page, settings=True)
        if callback.message:
            await callback.message.edit_text(text, reply_markup=keyboard)
        vault.commit_all("book reminder settings")
        await callback.answer("Настройка сохранена")
        return
    if action != "b" or len(parts) < 3:
        await callback.answer("Неизвестное действие", show_alert=True)
        return
    selected = books.get_book(parts[2])
    if selected is None:
        await callback.answer("Книга не найдена", show_alert=True)
        return
    uid = userctx.current_uid()
    if not ratelimit.try_acquire(uid):
        await callback.answer(ratelimit.BUSY_MESSAGE, show_alert=True)
        return
    try:
        excerpt = books.choose_excerpt(str(selected["id"]))
        bot_mood = moods.random_bot_mood()
        generated = await ask_book_question(
            title=str(selected.get("title") or selected["id"]),
            author=str(selected.get("author") or ""),
            excerpt=excerpt,
            bot_mood=bot_mood,
        )
        question = str(generated["question"])
        current = session.start(mode="probe", domain="knowledge")
        q_num = vault.next_q_num()
        metadata = {
            "source": "book",
            "book_id": selected["id"],
            "title": selected.get("title"),
            "author": selected.get("author"),
            "excerpt": excerpt,
        }
        session.set_question(question, "knowledge", q_num=q_num, metadata=metadata)
        current.main_question = question
        current.main_q_num = q_num
        session.persist()
        await session_messages.send_question(
            callback.bot,
            callback.message.chat.id if callback.message else callback.from_user.id,
            q_num=q_num,
            mode="probe",
            domain="knowledge",
            text=question,
            bot_mood=bot_mood,
            event_kind="book_question",
            metadata=metadata,
        )
        vault.commit_all("book question")
        await callback.answer()
    except (books.BookError, LLMError):
        log.warning("book question unavailable")
        await callback.answer("Не удалось начать разговор", show_alert=True)
    finally:
        ratelimit.release(uid)


@router.message(Command("like"))
async def cmd_like(message: Message) -> None:
    _record_command(message)
    if message.reply_to_message is None:
        await message.answer("Ответь командой /like на реплику Иуды.")
        return
    action = _action_for_message(message.reply_to_message.message_id)
    if action is None or not face_actions.is_rateable(action):
        await message.answer("Не нашёл подходящую реплику Иуды.")
        return
    token = str(action["token"])
    was_liked = face_actions.is_liked(token)
    face_actions.set_liked(token, liked=True, at=message.date)
    face_actions.record_user_score(token, 1.0, "favorite", at=message.date)
    if not was_liked:
        moods.record_mask_like(action.get("bot_mood"), at=message.date)
    vault.commit_all("liked reply")
    await message.answer("В избранном.")


@router.message(Command("regen"))
async def cmd_regen(message: Message, command: CommandObject) -> None:
    _record_command(message)
    if message.reply_to_message is None:
        await message.answer("Ответь командой /regen на реплику Иуды.")
        return
    action = _action_for_message(message.reply_to_message.message_id)
    if action is None or not face_actions.is_rateable(action):
        await message.answer("Не нашёл комментарий Иуды для перегенерации.")
        return
    requested = (command.args or "").strip().lower().replace(" ", "_")
    if requested and requested not in moods.BOT_MOODS:
        await message.answer("Не знаю такую маску. Доступные: " + ", ".join(moods.BOT_MOODS))
        return
    used = face_actions.used_bot_moods(str(action["token"]))
    face = requested or moods.opposite_bot_mood(action.get("bot_mood"), exclude=used)
    if not face or face in used:
        await message.answer("Все подходящие маски уже использованы.")
        return
    hydrated = face_actions.hydrate_action(action)
    if not hydrated["user_text"]:
        await message.answer("Не нашёл исходный ответ человека.")
        return
    uid = userctx.current_uid()
    if not ratelimit.try_acquire(uid):
        await message.answer(ratelimit.BUSY_MESSAGE)
        return
    try:
        new_text = await regenerate_reaction(
            hydrated["question"],
            hydrated["user_text"],
            bot_mood=face,
        )
    except LLMError:
        new_text = moods.llm_error_fallback_reply()
    finally:
        ratelimit.release(uid)
    session_id = action.get("session_id")
    if not session_id:
        await message.answer("Не нашёл session-log этой реплики.")
        return
    token = face_actions.create_action(
        session_id=str(session_id),
        q_num=action.get("q_num"),
        answered_q_num=action.get("answered_q_num"),
        kind="regen",
        bot_mood=face,
        assistant_text=new_text,
        user_text=hydrated["user_text"],
        question=hydrated["question"],
        session_context="",
        reply_to_user_message_id=action.get("reply_to_user_message_id"),
        parent_token=str(action["token"]),
        at=message.date,
    )
    sent = await message.answer(
        session_messages.with_face_signature(new_text, face),
        parse_mode="HTML",
        reply_to_message_id=message.reply_to_message.message_id,
    )
    event = session_log.append_required(
        session_id=str(session_id),
        role="assistant",
        kind="regen",
        text=new_text,
        at=sent.date,
        message_id=sent.message_id,
        reply_to_message_id=message.reply_to_message.message_id,
        q_num=action.get("q_num"),
        domain=action.get("domain"),
        bot_mood=face,
    )
    face_actions.set_message(token, sent.message_id, at=sent.date)
    vault.commit_all(f"regen {event['event_id']}")


@router.message(Command("remask"))
async def cmd_remask(message: Message) -> None:
    _record_command(message)
    if message.reply_to_message is None:
        await message.answer("Ответь командой /remask на вопрос или реплику Иуды.")
        return
    event = session_log.find_assistant_event_by_message_id(message.reply_to_message.message_id)
    if event is None:
        await message.answer("Не нашёл сообщение Иуды.")
        return
    existing = face_actions.find_by_message_id(message.reply_to_message.message_id)
    token = face_actions.create_remask_action(
        event,
        parent_token=existing.get("token") if existing else None,
        at=message.date,
    )
    await message.answer(
        "Выбери новое лицо Иуды.",
        reply_to_message_id=message.reply_to_message.message_id,
        reply_markup=_remask_keyboard(token),
    )
    vault.commit_all("remask menu")


@router.callback_query(F.data.startswith("face:"))
async def cb_face_action(callback: CallbackQuery) -> None:
    parts = (callback.data or "").split(":")
    if len(parts) != 4 or parts[1] != "rm":
        await callback.answer("Неизвестное действие", show_alert=True)
        return
    action = face_actions.get_action(parts[2])
    try:
        face = moods.BOT_MOODS[int(parts[3])]
    except (ValueError, IndexError):
        face = None
    if action is None or face is None:
        await callback.answer("Эта кнопка устарела", show_alert=True)
        return
    event = session_log.find_event(action.get("assistant_event_id"))
    if event is None:
        await callback.answer("Сообщение не найдено", show_alert=True)
        return
    try:
        await callback.bot.edit_message_text(
            chat_id=callback.message.chat.id if callback.message else callback.from_user.id,
            message_id=int(action["message_id"]),
            text=session_messages.event_with_face(event, face),
            parse_mode="HTML",
        )
    except Exception:
        log.exception("failed to edit remasked message")
    session_log.set_event_bot_mood(str(event["event_id"]), face)
    face_actions.set_bot_mood(parts[2], face)
    if action.get("parent_token"):
        face_actions.set_bot_mood(str(action["parent_token"]), face)
    if callback.message:
        try:
            await callback.message.edit_text(f"Маска выбрана: {face}")
        except Exception:
            pass
    vault.commit_all("remask")
    await callback.answer("Маску сменил.")


@router.message(Command("leta"))
async def cmd_leta(message: Message, command: CommandObject) -> None:
    uid = int(userctx.current_uid() or 0)
    expected = deletion_service.confirmation_args(uid)
    if (command.args or "").strip() != expected:
        await message.answer(
            "Это удалит только твою базу raw/mood/personality и книжные настройки. "
            "Общие книги и данные других людей останутся.\n\n"
            f"Для подтверждения отправь:\n<code>{html.escape(deletion_service.confirmation_command(uid))}</code>",
            parse_mode="HTML",
        )
        return
    message_ids = deletion_service.collect_chat_message_ids(
        extra_ids=[message.message_id],
        fill_until_message_id=message.message_id,
    )
    try:
        deletion_service.delete_current_user_data()
    except VaultError:
        await message.answer("Не удалил: проверка безопасности не прошла.")
        return
    await _delete_chat_messages_after_leta(message, message_ids)
    await message.answer("Кончил.")


async def _delete_chat_messages_after_leta(
    message: Message,
    message_ids: list[int | None],
    *,
    delete_delay: float | None = None,
) -> None:
    delay = LETA_CHAT_PURGE_DELAY_SECONDS if delete_delay is None else delete_delay
    for message_id in sorted({int(value) for value in message_ids if value is not None}):
        try:
            await message.bot.delete_message(chat_id=message.chat.id, message_id=message_id)
        except Exception:
            log.debug("telegram chat purge skipped message_id=%s", message_id)
        if delay > 0:
            await asyncio.sleep(delay)


@admin_router.message(Command("adduser"))
async def cmd_adduser(message: Message, command: CommandObject) -> None:
    if not _is_owner(message):
        return
    try:
        uid = int((command.args or "").strip())
    except ValueError:
        await message.answer("Использование: /adduser <telegram_user_id>")
        return
    if uid <= 0 or uid > 10**15:
        await message.answer("Некорректный id.")
        return
    added = users.add_user(uid, by=message.from_user.id)
    await message.answer(f"Пользователь {uid} {'добавлен' if added else 'уже в списке'}.")


@admin_router.message(Command("removeuser"))
async def cmd_removeuser(message: Message, command: CommandObject) -> None:
    if not _is_owner(message):
        return
    try:
        uid = int((command.args or "").strip())
    except ValueError:
        await message.answer("Использование: /removeuser <telegram_user_id>")
        return
    if users.is_owner(uid):
        await message.answer("Нельзя убрать владельца.")
        return
    removed = users.remove_user(uid)
    await message.answer(
        f"Пользователь {uid} убран; его данные сохранены."
        if removed
        else f"Пользователя {uid} не было в списке."
    )


@admin_router.message(Command("users"))
async def cmd_users(message: Message) -> None:
    if not _is_owner(message):
        return
    lines = [f"Владелец: {message.from_user.id}", "Доверенные:"]
    lines.extend(
        f"• {item.get('id')} (consent {'✓' if item.get('consent') else '—'})"
        for item in users.list_users()
    )
    await message.answer("\n".join(lines))


def _reply_targets_pending(message: Message, pending: dict) -> bool:
    if message.reply_to_message is None:
        return True
    try:
        return int(message.reply_to_message.message_id) == int(pending.get("message_id"))
    except (TypeError, ValueError):
        return False


async def _open_book_reminder(message: Message, pending: dict) -> None:
    consumed = books.consume_pending_reminder()
    if consumed is None:
        return
    current = session.start(mode="probe", domain="knowledge")
    q_num = vault.next_q_num()
    excerpt = str(consumed.get("excerpt") or "")
    metadata = {
        "source": "book_reminder",
        "book_id": consumed.get("book_id"),
        "title": consumed.get("title"),
        "author": consumed.get("author"),
        "excerpt": excerpt,
        "reminder_event_id": consumed.get("raw_event_id"),
    }
    session.set_question(excerpt, "knowledge", q_num=q_num, metadata=metadata)
    current.main_question = excerpt
    current.main_q_num = q_num
    session_log.append_required(
        session_id=current.id,
        role="assistant",
        kind="book_question",
        text=excerpt,
        q_num=q_num,
        domain="knowledge",
        metadata=metadata,
    )
    session.persist()
    vault.commit_all("book reminder consumed")


@router.message(F.text.startswith("/"))
async def unknown_command(message: Message) -> None:
    await message.answer("Не знаю такой команды. Список доступных — /help.")


@router.message(F.text)
async def on_text(message: Message) -> None:
    clean = await _accept_text(message, message.text or "")
    if clean is None:
        return

    if message.reply_to_message is not None:
        session_id = session_log.find_session_by_message_id(message.reply_to_message.message_id)
        if session_id is not None:
            session.resume(session_id)

    pending = books.pending_reminder()
    if pending and _reply_targets_pending(message, pending):
        await _open_book_reminder(message, pending)

    if session.get() is None or not session.get().last_question:
        await _ingest_note(message, clean, source="plain_text")
        return
    await _process_current_text(message, clean)


async def send_daily_question(bot: Bot, uid: int) -> bool:
    """Совместимый публичный вход для scheduler и старых интеграций."""
    from .services.daily_service import send_daily_question as send

    return await send(bot, uid)
