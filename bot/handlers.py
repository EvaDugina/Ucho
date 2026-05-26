import html
import logging
import random
import re

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from . import (
    about,
    face_actions,
    mood_file,
    moods,
    qmap,
    questions,
    ratelimit,
    session,
    session_log,
    sessions,
    userctx,
    users,
    vault,
)
from .config import ALLOWED_TELEGRAM_IDS, DOMAINS, OWNER_TELEGRAM_ID
from .errors import LLMError
from .llm import about_present, ask_next, regenerate_reaction
from .services import conversation_service, daily_service, note_service, session_messages
from .validation import (
    MAX_USER_TEXT,
    safe_chat_html,
    safe_user_text,
)

log = logging.getLogger(__name__)
# Основной роутер — пользовательские команды + текстовый поток (on_text).
router = Router()
# Админ-команды владельца — отдельным роутером для организации. Включается ПЕРВЫМ
# (до router), чтобы команды матчились здесь раньше catch-all on_text(F.text).
# Гейтинг доступа — внутренний `_is_owner` в каждом хэндлере (не router-фильтр:
# фильтр уронил бы команду гостя в on_text как текст-заметку — см. cmd_adduser).
admin_router = Router()

_DOMAIN_LABELS = {
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

TG_MSG_LIMIT = session_messages.TG_MSG_LIMIT  # запас от 4096

# Сентинел для домена, помеченного пользователем (/requestion). В DOMAINS его нет —
# он влияет только на отображение «пользовательский» в сообщении бота. LLM на этот
# домен не получает хинт, чтобы он сам выбрал реальный домен для концептов.
USER_DOMAIN = session_messages.USER_DOMAIN
USER_DOMAIN_LABEL = session_messages.USER_DOMAIN_LABEL

# Заголовок вопроса в сообщении бота: "Q<N> · [mode ·] <domain>" (см. _format_q).
_Q_HEAD_RE = re.compile(r"^Q(\d+)\s*·\s")


def _parse_question_message(text: str | None) -> dict | None:
    """Восстановить вопрос из тела процитированного (reply) сообщения бота.

    Фолбэк, когда qmap не знает message_id (вопрос задан до появления qmap,
    либо карта потерялась): сам текст сообщения несёт всё нужное — номер, домен
    и формулировку. Формат — из ``_format_q``: "Q<N> · <domain>\\n\\n<тело>".
    Возвращает ``{q_num, domain, text}`` или None, если это не вопрос-сообщение.
    """
    if not text:
        return None
    parts = text.split("\n\n", 1)
    if len(parts) != 2:
        return None
    head, body = parts[0].strip(), parts[1].strip()
    if not _Q_HEAD_RE.match(head):
        return None
    tokens = [t.strip() for t in head.split("·")]
    try:
        q_num = int(tokens[0][1:])  # "Q42" → 42
    except (ValueError, IndexError):
        return None
    label = tokens[-1]
    if label == USER_DOMAIN_LABEL:
        domain = USER_DOMAIN
    elif label in DOMAINS:
        domain = label
    else:
        return None  # домен не распознан — не реконструируем
    # Хвост "(повтор QN)" от /requestion в тело не тащим.
    body = re.sub(r"\n*\(повтор\s+Q\d+\)\s*$", "", body).strip()
    if not body:
        return None
    return {"q_num": q_num, "domain": domain, "text": body}


def _ask_keyboard() -> InlineKeyboardMarkup:
    """2 кнопки в ряд для всех доменов, плюс отдельный ряд «на выбор бота»."""
    rows: list[list[InlineKeyboardButton]] = []
    pair: list[InlineKeyboardButton] = []
    for d in DOMAINS:
        pair.append(InlineKeyboardButton(text=_DOMAIN_LABELS.get(d, d), callback_data=f"ask:{d}"))
        if len(pair) == 2:
            rows.append(pair)
            pair = []
    if pair:
        rows.append(pair)
    rows.append([InlineKeyboardButton(text="Пусть бот выберет сам", callback_data="ask:any")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _face_keyboard(token: str, *, liked: bool = False) -> InlineKeyboardMarkup:
    """Админская клавиатура: регенерация лиц, feedback маски, избранное."""
    rows: list[list[InlineKeyboardButton]] = []
    pair: list[InlineKeyboardButton] = []
    for idx, face in enumerate(moods.BOT_MOODS):
        pair.append(InlineKeyboardButton(text=face, callback_data=f"face:rg:{token}:{idx}"))
        if len(pair) == 2:
            rows.append(pair)
            pair = []
    if pair:
        rows.append(pair)
    rows.append([
        InlineKeyboardButton(text="✓ маска подходит", callback_data=f"face:ok:{token}"),
        InlineKeyboardButton(text="✗ маска не подходит", callback_data=f"face:no:{token}"),
    ])
    rows.append([
        InlineKeyboardButton(
            text=("★ понравилось" if liked else "☆ понравилось"),
            callback_data=f"face:like:{token}",
        )
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _remask_keyboard(token: str) -> InlineKeyboardMarkup:
    """Админская клавиатура выбора лица для уже отправленного bot-сообщения."""
    rows: list[list[InlineKeyboardButton]] = []
    pair: list[InlineKeyboardButton] = []
    for idx, face in enumerate(moods.BOT_MOODS):
        pair.append(InlineKeyboardButton(text=face, callback_data=f"face:rm:{token}:{idx}"))
        if len(pair) == 2:
            rows.append(pair)
            pair = []
    if pair:
        rows.append(pair)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _with_face_signature(text: str, bot_mood: str | None) -> str:
    return session_messages.with_face_signature(text, bot_mood)


def _question_field_with_face(text: str, bot_mood: str | None) -> str:
    """Plain-text question field для LLM/raw: текст + выбранная маска без HTML."""
    return session_messages.question_field_with_face(text, bot_mood)


def _render_event_with_face(event: dict, bot_mood: str) -> str:
    """HTML-тело уже существующего bot-сообщения после remask."""
    return session_messages.event_with_face(event, bot_mood)


# ---------- helpers ----------


def _is_owner(message: Message) -> bool:
    from_user = getattr(message, "from_user", None)
    return from_user is not None and users.is_owner(from_user.id)


def _is_allowed(message: Message) -> bool:
    from_user = getattr(message, "from_user", None)
    return from_user is not None and users.is_allowed(from_user.id)


_DIRECTION_RU = {"auto": "на себя", "hetero": "на других/мир", "neutral": "нейтрально"}


def _format_mood(mv: dict, bot_mood: str | None, vad: dict | None = None) -> str:
    return conversation_service.format_mood(mv, bot_mood, vad)


def _llm_user_message(exc: LLMError, fallback: str) -> str:
    """Человекочитаемое сообщение о сбое LLM без технического traceback."""
    return getattr(exc, "user_message", None) or fallback


def _format_q(q_num: int, mode: str, domain: str, question_text: str) -> str:
    """Сформировать HTML-сообщение с вопросом.

    Заголовок: Q42 · [mode ·] <i>domain</i>
    Тело: <code>…</code> — inline-моноширинный шрифт. Telegram также даёт
    long-press «копировать» на <code>.
    Отправлять с parse_mode='HTML'.

    Все динамические подстановки экранируются через ``html.escape`` —
    защита от HTML-injection из ответа LLM (Telegram не должен интерпретировать
    LLM-вывод как разметку).

    ``question_text`` обрезается до ~3500 символов — Telegram режет по 4096
    байт, нужен запас на head + теги ``<code>``.
    """
    return session_messages.format_q(q_num, mode, domain, question_text)


def _real_domain(d: str | None) -> str | None:
    """Возвращает d только если это валидный концептный домен. Иначе None."""
    return conversation_service.real_domain(d)


async def _send_question(
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
    return await session_messages.send_question(
        bot, chat_id,
        q_num=q_num,
        mode=mode,
        domain=domain,
        text=text,
        suffix=suffix,
        plain=plain,
        bot_mood=bot_mood,
        admin_controls=admin_controls,
        action_context=action_context,
    )


def _open_anchored_session(entry: dict) -> None:
    """Открыть probe-сессию, заякоренную на вопрос из qmap.

    Текущая сессия (если была) молча закрывается — `session.start` затирает
    слот. `clarifier_count` сбрасывается в 0.

    Q-номер: исходный, если вопрос ещё НЕ отвечен (первый ответ пишется под
    его собственным номером); новый `next_q_num()`, если уже отвечен — тогда
    это Q-повтор, отдельный raw-блок, старый ответ цел.
    """
    text = entry["text"]
    domain = entry["domain"]
    q_num = vault.next_q_num() if entry.get("answered") else int(entry["q_num"])
    start_domain = domain if domain in DOMAINS else None
    session.start(mode="probe", domain=start_domain)
    s = session.get()
    if s is None:
        return
    session.set_question(text, domain, q_num=q_num)
    s.main_question = text
    s.main_q_num = q_num
    s.clarifier_count = 0
    session.persist()
    s.record_assistant(text, at=entry.get("ts"))


def _anchor_user_cmd(message: Message) -> None:
    """Записать пользовательское сообщение как первое в активной сессии.

    Команда или произвольная заметка вне активной сессии открывает обсуждение.
    Первое user-событие нужно для полного transcript и reply-resume.
    """
    s = session.get()
    mid = getattr(message, "message_id", None)
    if s is not None and mid:
        raw_text = message.text or ""
        s.add_message_id(int(mid))
        session_log.append(
            session_id=s.id,
            role="user",
            kind="command" if raw_text.startswith("/") else "note_open",
            text=raw_text,
            at=getattr(message, "date", None),
            message_id=int(mid),
        )


async def _session_reply(
    message: Message,
    text: str,
    *,
    anchor: str | None = None,
    domain: str | None = None,
    set_anchor: bool = True,
    **answer_kw,
) -> Message | None:
    """Ответ команды В РАМКАХ активной сессии: записать message_id бота (для
    reply-resume) и (если ``set_anchor``) сделать текст/``anchor`` якорем
    следующего хода — тогда reply/продолжение пойдёт как обычный ответ → реакция.
    """
    sent = await message.answer(text, **answer_kw)
    s = session.get()
    if s is None:
        return sent
    if sent is not None and getattr(sent, "message_id", None):
        s.add_message_id(int(sent.message_id))
    if set_anchor:
        dom = domain if domain in DOMAINS else (s.last_domain if s.last_domain in DOMAINS else "everyday")
        a = anchor if anchor is not None else text
        session.set_question(a, dom, q_num=vault.next_q_num())
        s.record_assistant(html.unescape(text or ""), at=getattr(sent, "date", None))
    session_log.append_required(
        session_id=s.id,
        role="assistant",
        kind="service",
        text=html.unescape(text or ""),
        at=getattr(sent, "date", None),
        message_id=getattr(sent, "message_id", None),
        q_num=s.current_q_num,
        domain=s.last_domain,
    )
    return sent


async def _accept_user_text(message: Message, raw: str) -> str | None:
    """Принять текст пользователя для записи/обработки.

    * Применяет ``safe_user_text`` (нормализация переводов строк, отсечение
      control-символов, обрезка по ``MAX_USER_TEXT``).
    * Если был обрезан — отвечает пользователю предупреждением и логирует.
    * Возвращает очищенный текст или None если он пустой после санитизации.
    """
    text, truncated = safe_user_text(raw)
    if not text:
        await message.answer("Пустой ответ. Напиши хоть что-то или /start (смыв).")
        return None
    if truncated:
        vault.append_log("warn", "user_text_truncated", f"len(raw)={len(raw)} > {MAX_USER_TEXT}")
        await message.answer(
            f"⚠ Ответ был длиннее {MAX_USER_TEXT} символов — обрезал, чтобы влезло в контекст."
        )
    return text


# ---------- thinking / spinner ----------

# Один и тот же стикер всегда — 🎰 (slot-machine dice). Не выбираем случайно.
_THINKING_EMOJI = "🎰"


async def _start_thinking_chat(bot: Bot, chat_id: int) -> Message | None:
    """Индикатор «думаю» по bot+chat_id: один стикер 🎰 + удаляемый текст «Думаю.».

    Стикер 🎰 (dice) Telegram удалять не даёт — остаётся как маркер хода мысли;
    возвращаем Message текстового «Думаю.» для последующего удаления.
    Показывается ТОЛЬКО при генерации главного вопроса (/ask) и портрета (/about).
    """
    try:
        await bot.send_chat_action(chat_id, "typing")
    except Exception:
        pass
    try:
        await bot.send_dice(chat_id, emoji=_THINKING_EMOJI)
    except Exception:
        log.exception("failed to send dice indicator")
    try:
        return await bot.send_message(chat_id, "Думаю.")
    except Exception:
        log.exception("failed to send thinking placeholder")
        return None


async def _start_thinking(message: Message) -> Message | None:
    return await _start_thinking_chat(message.bot, message.chat.id)


async def _stop_thinking(thinking: Message | None) -> None:
    if thinking is None:
        return
    try:
        await thinking.delete()
    except Exception:
        log.exception("failed to delete thinking message")


def _split_for_telegram(text: str) -> list[str]:
    return session_messages.split_for_telegram(text)


def _recent_raw_text(days: int = 7, max_chars: int = 8000) -> str:
    return conversation_service.recent_raw_text(days=days, max_chars=max_chars)


def _context_for_domain(domain: str | None) -> str:
    return conversation_service.context_for_domain(domain)


# ---------- commands ----------


# Тело help собирается в cmd_help: основные группы → [Админ, если владелец] →
# /pebble → футер.
_HELP_BODY = (
    "<b>Ухо</b> — узор ушной раковины из тех слов, которые я слышу.\n"
    "\n"
    "<b>Спросить себя</b>\n"
    "<b>/ucho</b> <i>&lt;текст&gt;</i> — свободная заметка → в граф\n"
    "<b>/echo</b> <i>&lt;вопрос&gt;</i> — твой собственный вопрос\n"
    "<b>/ask</b> <i>[тема]</i> — вопрос; без темы покажу кнопки\n"
    "<b>/requestion</b> <i>N</i> — повторить выбранный вопрос №N\n"
    "<b>/about</b> — каким я тебя вижу\n"
    "\n"
    "<b>Сервис</b>\n"
    "<b>/start</b> — смыв: закрыть сессию (данные целы)\n"
    "<b>/help</b> — этот список\n"
    "<b>/history</b> — последние вопросы"
)

_HELP_ADMIN = (
    "<b>Админ</b>\n"
    "<b>/adduser</b> <i>id</i> — добавить пользователя\n"
    "<b>/removeuser</b> <i>id</i> — убрать (данные не удаляются)\n"
    "<b>/users</b> — список доверенных\n"
    "<b>/dailyall</b> — разослать дневной вопрос всем прямо сейчас\n"
    "<b>/like</b> — отметить reply-реплику Иуды\n"
    "<b>/remask</b> — сменить лицо reply-вопросу или комментарию Иуды"
)

_HELP_PEBBLE = "<b>/pebble</b> — бросить камень → «буль.»"

_HELP_FOOTER = (
    f"<b>Домены:</b> <code>{', '.join(DOMAINS)}</code>\n\n"
    "На любое моё сообщение можно ответить через <b>reply</b> (смахни сообщение) — "
    "продолжим с того места.\n\n"
    "<i>Я сам настигну тебя своим вопросом.</i>"
)


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    """Кнопка смыва: закрывает текущую сессию. Данные (граф/raw) не трогает."""
    if not _is_allowed(message):
        log.info("ignored /start from non-owner user_id=%s", message.from_user.id if message.from_user else None)
        return
    vault.ensure_layout()
    # Активную сессию уже закрыл AccessMiddleware (любая команда закрывает её,
    # снапшот ушёл в кольцо — можно продолжить reply). Здесь только подтверждаем.
    session.clear()
    await message.answer(
        "Смыто — сессия закрыта (если была). Данные целы; продолжить разговор можно "
        "reply на любое его сообщение.\nСписок команд — /help."
    )


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    if not _is_allowed(message):
        return
    parts = [_HELP_BODY]
    if _is_owner(message):
        parts.append(_HELP_ADMIN)  # после основных групп
    parts.append(_HELP_PEBBLE)     # /pebble — отдельной группой, после админских
    parts.append(_HELP_FOOTER)
    await message.answer("\n\n".join(parts), parse_mode="HTML")


# ---------- админ-команды (только владелец) ----------


@admin_router.message(Command("adduser"))
async def cmd_adduser(message: Message, command: CommandObject) -> None:
    if not _is_owner(message):
        return
    arg = (command.args or "").strip()
    try:
        uid = int(arg)
    except ValueError:
        await message.answer("Использование: /adduser <telegram_user_id>")
        return
    if uid <= 0 or uid > 10**15:
        await message.answer("Некорректный id.")
        return
    added = users.add_user(uid, by=message.from_user.id)
    if added:
        await message.answer(f"Пользователь {uid} добавлен. Его база создастся при первом обращении.")
    else:
        await message.answer(f"Пользователь {uid} уже в списке.")


@admin_router.message(Command("removeuser"))
async def cmd_removeuser(message: Message, command: CommandObject) -> None:
    if not _is_owner(message):
        return
    arg = (command.args or "").strip()
    try:
        uid = int(arg)
    except ValueError:
        await message.answer("Использование: /removeuser <telegram_user_id>")
        return
    if users.is_owner(uid):
        await message.answer("Нельзя убрать владельца.")
        return
    removed = users.remove_user(uid)
    await message.answer(
        f"Пользователь {uid} убран из доступа. Данные в users/{uid}/ остались (бот не удаляет)."
        if removed else f"Пользователя {uid} не было в списке."
    )


@admin_router.message(Command("users"))
async def cmd_users(message: Message) -> None:
    if not _is_owner(message):
        return
    reg = users.list_users()
    lines = [f"Владелец: {message.from_user.id}", ""]
    if reg:
        lines.append("Доверенные:")
        for u in reg:
            consent = "✓" if u.get("consent") else "—"
            lines.append(f"• {u.get('id')} (с {u.get('added','?')}, consent {consent})")
    else:
        lines.append("Других пользователей нет.")
    await message.answer("\n".join(lines))


@admin_router.message(Command("dailyall"))
async def cmd_dailyall(message: Message, bot: Bot) -> None:
    if not _is_owner(message):
        return
    targets = set(users.allowed_ids()) | set(users.all_data_user_ids())
    targets.add(OWNER_TELEGRAM_ID)
    targets.update(ALLOWED_TELEGRAM_IDS)
    sent = skipped = failed = 0
    for uid in sorted(targets):
        try:
            # send_daily_question сам дедупит по дню (общий маркер с cron/догоном)
            # и не пропускает из-за активной сессии/прошлых ответов.
            if await send_daily_question(bot, uid):
                sent += 1
            else:
                skipped += 1
        except Exception:
            failed += 1
            log.exception("dailyall failed for uid=%s", uid)
    userctx.set_user(message.from_user.id)
    await message.answer(
        f"Разослал. Отправлено: {sent}, пропущено (уже было сегодня): {skipped}, ошибок: {failed}."
    )


@router.message(Command("ask"))
async def cmd_ask(message: Message, command: CommandObject) -> None:
    if not _is_allowed(message):
        return
    arg = (command.args or "").strip()
    if not arg:
        # Голый /ask — выбор темы кнопками.
        await message.answer("Выбери тему:", reply_markup=_ask_keyboard())
        return

    domain: str | None = None
    hint: str | None = None
    if arg.lower() in DOMAINS:
        # /ask <домен> — вопрос внутри названной темы.
        domain = arg.lower()
    else:
        # /ask <свободный текст> — затравка/контекст для генерации вопроса; домен
        # LLM подберёт сам. Текст санитизируем как пользовательский ввод.
        hint, truncated = safe_user_text(arg, limit=2000)
        if not hint:
            await message.answer("Использование: /ask [тема или о чём спросить]")
            return
        if truncated:
            await message.answer("⚠ Затравка была слишком длинной — обрезал.")

    uid = userctx.current_uid()
    if not ratelimit.try_acquire(uid):
        await message.answer(ratelimit.BUSY_MESSAGE)
        return
    try:
        session.start(mode="probe", domain=domain)
        _anchor_user_cmd(message)
        await _send_next_question(
            message.bot, message.chat.id, domain=domain, hint=hint, show_thinking=True,
        )
    finally:
        ratelimit.release(uid)


@router.callback_query(F.data.startswith("ask:"))
async def cb_ask_domain(callback: CallbackQuery) -> None:
    if not users.is_allowed(callback.from_user.id):
        await callback.answer()
        return
    userctx.set_user(callback.from_user.id)
    payload = (callback.data or "").split(":", 1)[1]
    # Whitelist: только 'any' или конкретный домен из закрытого списка.
    if payload != "any" and payload not in DOMAINS:
        await callback.answer("Неизвестная тема", show_alert=True)
        return
    domain = None if payload == "any" else payload
    if callback.message:
        try:
            label = _DOMAIN_LABELS.get(domain or "", "🎲 на выбор бота")
            await callback.message.edit_text(f"Домен: {label}")
        except Exception:
            log.exception("failed to clear ask keyboard")
    await callback.answer()
    chat_id = callback.message.chat.id if callback.message else callback.from_user.id
    uid = userctx.current_uid()
    if not ratelimit.try_acquire(uid):
        await callback.bot.send_message(chat_id, ratelimit.BUSY_MESSAGE)
        return
    try:
        session.start(mode="probe", domain=domain)
        await _send_next_question(callback.bot, chat_id, domain=domain, show_thinking=True)
    finally:
        ratelimit.release(uid)


@router.callback_query(F.data.startswith("face:"))
async def cb_face_action(callback: CallbackQuery) -> None:
    """Админские кнопки лица: регенерация, feedback маски, избранное."""
    if not users.is_owner(callback.from_user.id):
        await callback.answer()
        return
    userctx.set_user(callback.from_user.id)
    parts = (callback.data or "").split(":")
    if len(parts) < 3:
        await callback.answer("Неизвестное действие", show_alert=True)
        return
    action = parts[1]
    token = parts[2]
    rec = face_actions.get_action(token)
    if rec is None:
        await callback.answer("Эта кнопка уже устарела", show_alert=True)
        return

    if action == "rm":
        if len(parts) < 4:
            await callback.answer("Неизвестное лицо", show_alert=True)
            return
        try:
            face_idx = int(parts[3])
            face = moods.BOT_MOODS[face_idx]
        except (ValueError, IndexError):
            await callback.answer("Неизвестное лицо", show_alert=True)
            return
        if rec.get("kind") != "remask":
            await callback.answer("Эта кнопка не для remask", show_alert=True)
            return
        event = session_log.find_event(rec.get("assistant_event_id"))
        if not event or event.get("role") != "assistant":
            await callback.answer("Не нашёл вопрос или комментарий бота", show_alert=True)
            return

        edited = False
        parent_token = rec.get("parent_token")
        target_markup = None
        if parent_token:
            target_markup = _face_keyboard(parent_token, liked=face_actions.is_liked(parent_token))
        try:
            await callback.bot.edit_message_text(
                chat_id=callback.message.chat.id if callback.message else callback.from_user.id,
                message_id=int(rec.get("message_id")),
                text=_render_event_with_face(event, face),
                parse_mode="HTML",
                reply_markup=target_markup,
            )
            edited = True
        except Exception:
            log.exception("failed to edit remasked message")

        session_log.set_event_bot_mood(rec.get("assistant_event_id"), face)
        face_actions.set_bot_mood(token, face)
        if parent_token:
            face_actions.set_bot_mood(parent_token, face)

        s = session.get()
        if (
            s is not None
            and s.id == event.get("session_id")
            and event.get("q_num") is not None
            and s.current_q_num == int(event.get("q_num"))
        ):
            s.last_question = _question_field_with_face(str(event.get("text") or ""), face)
            session.persist()

        try:
            if callback.message:
                await callback.message.edit_text(f"Маска выбрана: {face}")
        except Exception:
            log.exception("failed to close remask menu")
        vault.commit_all("remask")
        await callback.answer("Маску сменил." if edited else "Маску записал, но сообщение не изменилось.")
        return

    if action in {"ok", "no"}:
        verdict = "suitable" if action == "ok" else "unsuitable"
        if face_actions.record_mood_feedback(token, verdict):
            vault.commit_all("mood feedback")
            await callback.answer("Отметил маску.")
        else:
            await callback.answer("Не смог отметить маску", show_alert=True)
        return

    if action == "like":
        liked = face_actions.set_liked(token, liked=None)
        if liked is None:
            await callback.answer("Не нашёл реплику Иуды для отметки", show_alert=True)
            return
        try:
            if callback.message:
                await callback.message.edit_reply_markup(reply_markup=_face_keyboard(token, liked=liked))
        except Exception:
            log.exception("failed to update like keyboard")
        vault.commit_all("liked reply")
        await callback.answer("Сохранил в понравившиеся." if liked else "Снял отметку.")
        return

    if action != "rg" or len(parts) < 4:
        await callback.answer("Неизвестное действие", show_alert=True)
        return
    try:
        face_idx = int(parts[3])
        face = moods.BOT_MOODS[face_idx]
    except (ValueError, IndexError):
        await callback.answer("Неизвестное лицо", show_alert=True)
        return
    uid = userctx.current_uid()
    if not ratelimit.try_acquire(uid):
        await callback.answer()
        await callback.bot.send_message(callback.from_user.id, ratelimit.BUSY_MESSAGE)
        return
    try:
        try:
            action_texts = face_actions.hydrate_action(rec)
            new_text = await regenerate_reaction(
                action_texts.get("question") or "",
                action_texts.get("user_text") or "",
                bot_mood=face,
                session_context=action_texts.get("session_context") or "",
            )
        except LLMError as exc:
            await callback.answer(_llm_user_message(exc, "Не получилось перегенерировать."), show_alert=True)
            return
        if not new_text:
            new_text = "Складно. Слишком складно."
        new_token = face_actions.create_action(
            session_id=rec.get("session_id"),
            q_num=rec.get("q_num"),
            answered_q_num=rec.get("answered_q_num"),
            kind="regen",
            bot_mood=face,
            assistant_text=new_text,
            user_text=action_texts.get("user_text") or "",
            question=action_texts.get("question") or "",
            session_context=action_texts.get("session_context") or "",
            reply_to_user_message_id=rec.get("reply_to_user_message_id"),
            parent_token=token,
        )
        chat_id = callback.message.chat.id if callback.message else callback.from_user.id
        sent = await callback.bot.send_message(
            chat_id,
            _with_face_signature(new_text, face),
            parse_mode="HTML",
            reply_to_message_id=rec.get("message_id"),
            reply_markup=_face_keyboard(new_token),
        )
        s = session.get()
        log_session_id = s.id if s is not None else rec.get("session_id")
        log_domain = s.last_domain if s is not None else rec.get("domain")
        if s is not None:
            try:
                s.add_message_id(sent.message_id)
                s.record_assistant(new_text, at=getattr(sent, "date", None))
            except Exception:
                log.exception("failed to record regen in session")
        if log_session_id:
            session_log.append(
                session_id=log_session_id,
                role="assistant",
                kind="regen",
                text=new_text,
                at=getattr(sent, "date", None),
                message_id=getattr(sent, "message_id", None),
                reply_to_message_id=rec.get("message_id"),
                q_num=rec.get("q_num"),
                domain=log_domain,
                bot_mood=face,
            )
        face_actions.set_message(new_token, getattr(sent, "message_id", None), at=getattr(sent, "date", None))
        vault.commit_all("face regen")
        await callback.answer("Перегенерировал.")
    finally:
        ratelimit.release(uid)


@router.message(Command("history"))
async def cmd_history(message: Message) -> None:
    """Последние 25 ГЛАВНЫХ вопросов (без ответов, без реакций/якорей).
    Свою сессию НЕ открывает (закрыта middleware) — сообщение после /history без
    reply уйдёт как /ucho."""
    if not _is_allowed(message):
        return
    items = questions.recent(25)
    if not items:
        await message.answer("История пуста. /ask чтобы начать.")
        return
    lines = [f"📜 Последние вопросы: {len(items)}.", ""]
    for e in items:
        q = e.get("text", "")
        if len(q) > 200:
            q = q[:200].rstrip() + "…"
        ts = (e.get("ts") or "").replace("T", " ")[:16]
        lines.append(f"*Q{e.get('n')}* · {ts} · {e.get('domain', '')}")
        lines.append(f"❓ {q}")
        lines.append("")
    lines.append("Повторить вопрос: /requestion N")
    for chunk in _split_for_telegram("\n".join(lines)):
        await message.answer(chunk)


@router.message(Command("requestion"))
async def cmd_requestion(message: Message, command: CommandObject) -> None:
    """Повторить вопрос Q<N> — задаёт его заново как новый главный."""
    if not _is_allowed(message):
        return
    arg = (command.args or "").strip()
    try:
        n = int(arg)
    except ValueError:
        await message.answer("Использование: /requestion <номер>. Список: /history.")
        return
    if n <= 0 or n > 10**9:
        await message.answer("Номер вне разумного диапазона.")
        return
    entry = vault.find_question(n)
    if entry is None:
        await message.answer(f"Q{n} не найден. /history покажет доступные.")
        return

    new_n = vault.next_q_num()
    session.start(mode="probe", domain=entry["domain"])
    s = session.get()
    if s is None:  # на всякий случай
        return
    _anchor_user_cmd(message)
    session.set_question(entry["question"], entry["domain"], q_num=new_n)
    s.main_question = entry["question"]
    s.main_q_num = new_n
    s.clarifier_count = 0
    session.persist()
    await _send_question(
        message.bot, message.chat.id,
        q_num=new_n, mode="probe", domain=entry["domain"], text=entry["question"],
        suffix=f"\n<i>(повтор Q{n})</i>",
    )


@router.message(Command("echo"))
async def cmd_echo(message: Message, command: CommandObject) -> None:
    """Пользователь сам задаёт вопрос. Бот возвращает его как Q под меткой «пользовательский»."""
    if not _is_allowed(message):
        return
    raw = (command.args or "").strip()
    if not raw:
        await message.answer("Использование: /echo <текст твоего вопроса>")
        return
    text, truncated = safe_user_text(raw, limit=2000)
    if not text:
        await message.answer("Пустой вопрос после очистки. Попробуй ещё раз.")
        return
    if truncated:
        await message.answer("⚠ Вопрос был слишком длинным — обрезал.")

    new_n = vault.next_q_num()
    session.start(mode="probe", domain=None)
    s = session.get()
    if s is None:
        return
    _anchor_user_cmd(message)
    session.set_question(text, USER_DOMAIN, q_num=new_n)
    s.main_question = text
    s.main_q_num = new_n
    s.clarifier_count = 0
    session.persist()
    await _send_question(
        message.bot, message.chat.id,
        q_num=new_n, mode="probe", domain=USER_DOMAIN, text=text,
    )


@router.message(Command("pebble"))
async def cmd_pebble(message: Message) -> None:
    """Бросить камень: бот жив → «буль.». Прозрачен для сессии — НЕ открывает и
    НЕ закрывает её (исключён из close-on-command в AccessMiddleware), чтобы можно
    было проверить бота, пока ждёшь реакцию, и эта реакция не оборвалась."""
    if not _is_allowed(message):
        return
    await message.answer("буль.")


@router.message(Command("about"))
async def cmd_about(message: Message) -> None:
    """Показать пользователю его портрет (03_personality/about.md) — отформатированный
    отдельным промптом текст от 1-го лица. Пусто → честно скажем, что рано."""
    if not _is_allowed(message):
        return
    # /about открывает сессию-обсуждение: можно ответить (reply) на портрет.
    session.start(mode="probe", domain=None)
    _anchor_user_cmd(message)
    portrait = about.render_for_prompt(max_chars=4000)
    if not portrait:
        await _session_reply(
            message,
            "Я тебя ещё толком не распробовал — поговори со мной (/ask), и портрет нарастёт.",
            anchor="(твой портрет)",
        )
        return
    uid = userctx.current_uid()
    if not ratelimit.try_acquire(uid):
        await message.answer(ratelimit.BUSY_MESSAGE)
        return
    thinking = await _start_thinking(message)
    try:
        text = await about_present(portrait)
    except LLMError as exc:
        # Ожидаемый сбой модели → нейтральная фраза. Прочие исключения (баги)
        # не глушим: уходят в глобальный @dp.errors().
        log.warning("about_present LLM error")
        await _stop_thinking(thinking)
        await message.answer(_llm_user_message(exc, "Не вышло собрать портрет словами. Попробуй позже."))
        return
    finally:
        ratelimit.release(uid)
    await _stop_thinking(thinking)
    if not text:
        text = "Пока сказать почти нечего."
    # Вывод LLM экранируем перед нарезкой — портрет уходит как обычный текст
    # (parse_mode=HTML + html.escape), любой «код»/разметка не интерпретируется.
    text = safe_chat_html(text)
    chunks = _split_for_telegram(text)
    for chunk in chunks[:-1]:
        sent = await message.answer(chunk, parse_mode="HTML")
        s = session.get()
        if s is not None and sent is not None:
            s.add_message_id(int(sent.message_id))
            s.record_assistant(html.unescape(chunk), at=getattr(sent, "date", None))
            session_log.append(
                session_id=s.id,
                role="assistant",
                kind="service",
                text=html.unescape(chunk),
                at=getattr(sent, "date", None),
                message_id=getattr(sent, "message_id", None),
                q_num=s.current_q_num,
                domain=s.last_domain,
            )
    await _session_reply(message, chunks[-1], anchor="(твой портрет)", parse_mode="HTML")


async def _ingest_note(message: Message, clean: str, *, note_prefix: str | None = None) -> None:
    """Сохранить текст как свободную заметку (notes/) и прогнать через LLM в граф.

    Единый путь для /ucho и для текста, который не привязался к вопросу
    (неразрешённый reply / нет активной сессии) — чтобы осмысленный ответ
    пользователя НИКОГДА не терялся. ``note_prefix`` — пояснение, почему текст
    ушёл в заметку (для салвейдж-случаев).
    """
    # Per-user single-flight + cooldown: отклоняем до записи, чтобы заметка была
    # атомарной (на busy — ничего не сохранено, пользователь повторяет целиком).
    uid = userctx.current_uid()
    if not ratelimit.try_acquire(uid):
        await message.answer(ratelimit.BUSY_MESSAGE)
        return
    try:
        session.start(mode="probe", domain=None)
        _anchor_user_cmd(message)
        try:
            payload = await note_service.ingest_note(clean, at=getattr(message, "date", None))
        except Exception:
            log.exception("failed to ingest note")
            await message.answer("Не смог записать заметку. Ничего не разбираю — повтори позже.")
            return
        if payload is None:
            return
        await _send_question(
            message.bot, message.chat.id,
            q_num=payload.q_num, mode=payload.mode, domain=payload.domain, text=payload.text, plain=True,
            bot_mood=payload.bot_mood,
        )
    finally:
        ratelimit.release(uid)


@router.message(Command("ucho"))
async def cmd_ucho(message: Message, command: CommandObject) -> None:
    """Свободная заметка. Сохраняем verbatim в notes/<дата>.md и прогоняем через
    LLM process — концепты попадают в граф как черновики. Заметка работает как
    ответ без заданного вопроса.
    """
    if not _is_allowed(message):
        return
    raw = (command.args or "").strip()
    if not raw:
        await message.answer("Использование: /ucho <текст заметки>")
        return
    clean = await _accept_user_text(message, raw)
    if clean is None:
        return
    await _ingest_note(message, clean)


@router.message(Command("like"))
async def cmd_like(message: Message) -> None:
    """Отметить reply-реплику Иуды как понравившуюся / снять отметку."""
    if not _is_owner(message):
        return
    s = session.get()
    if s is not None:
        session_log.append(
            session_id=s.id,
            role="user",
            kind="command",
            text=message.text or "",
            at=getattr(message, "date", None),
            message_id=getattr(message, "message_id", None),
            reply_to_message_id=(
                message.reply_to_message.message_id if message.reply_to_message is not None else None
            ),
            q_num=s.current_q_num,
            domain=s.last_domain,
        )
    if message.reply_to_message is None:
        await message.answer("Ответь командой /like на реплику Иуды.")
        return
    rec = face_actions.find_by_message_id(message.reply_to_message.message_id)
    if rec is None:
        await message.answer("Не нашёл реплику Иуды для отметки")
        return
    liked = face_actions.set_liked(rec.get("token"), liked=None, at=getattr(message, "date", None))
    if liked is None:
        await message.answer("Не нашёл реплику Иуды для отметки")
        return
    vault.commit_all("liked reply")
    await message.answer("Сохранил в понравившиеся." if liked else "Снял отметку.")


@router.message(Command("remask"))
async def cmd_remask(message: Message) -> None:
    """Открыть меню выбора лица для reply-вопроса или reply-комментария Иуды."""
    if not _is_owner(message):
        return
    s = session.get()
    if s is not None:
        session_log.append(
            session_id=s.id,
            role="user",
            kind="command",
            text=message.text or "",
            at=getattr(message, "date", None),
            message_id=getattr(message, "message_id", None),
            reply_to_message_id=(
                message.reply_to_message.message_id if message.reply_to_message is not None else None
            ),
            q_num=s.current_q_num,
            domain=s.last_domain,
        )
    if message.reply_to_message is None:
        await message.answer("Ответь командой /remask на вопрос или комментарий Иуды.")
        return

    target_mid = message.reply_to_message.message_id
    event = session_log.find_assistant_event_by_message_id(target_mid)
    if event is None:
        await message.answer("Не нашёл вопрос или комментарий Иуды для смены маски.")
        return

    existing_action = face_actions.find_by_message_id(target_mid)
    parent_token = existing_action.get("token") if existing_action else None
    token = face_actions.create_remask_action(
        event,
        parent_token=parent_token,
        at=getattr(message, "date", None),
    )
    await message.answer(
        "Выбери новое лицо Иуды для этой реплики.",
        reply_to_message_id=target_mid,
        reply_markup=_remask_keyboard(token),
    )
    vault.commit_all("remask menu")


# ---------- text messages ----------


@router.message(F.text)
async def on_text(message: Message) -> None:
    if not _is_allowed(message):
        return
    s = session.get()
    text = (message.text or "").strip()

    # reply на сообщение сессии → продолжить именно ту сессию (даже закрытую, в
    # пределах последних 25). Если reply на сообщение текущей активной сессии —
    # просто продолжаем её. Иначе ищем в кольце и резюмируем.
    if message.reply_to_message is not None:
        rid = message.reply_to_message.message_id
        if s is not None and rid in (s.message_ids or []):
            pass  # reply внутри текущей сессии — обычный ход
        else:
            sid = sessions.find_by_message_id(rid)
            if sid is not None and session.resume(sid) is not None:
                s = session.get()
                vault.append_log("info", "session_resumed", f"sid={sid[:8]} by reply")

    # Фолбэк: reply на старый вопрос вне кольца — резолвим по карте message_id→вопрос.
    if message.reply_to_message is not None and (
        s is None or message.reply_to_message.message_id not in (s.message_ids or [])
    ):
        entry = qmap.find_by_message_id(message.reply_to_message.message_id)
        if entry is None:
            # qmap не знает это сообщение (вопрос задан до qmap / карта потерялась).
            # Фолбэк: восстанавливаем вопрос прямо из тела процитированного сообщения.
            parsed = _parse_question_message(message.reply_to_message.text)
            if parsed is not None:
                # В raw → уже отвечен → новый ответ станет Q-повтором (старый цел);
                # иначе — отвечаем под исходным номером.
                parsed["answered"] = vault.find_question(parsed["q_num"]) is not None
                vault.append_log(
                    "info", "reply_reconstructed",
                    f"Q{parsed['q_num']} {parsed['domain']} answered={parsed['answered']}",
                )
                entry = parsed
        if entry is None:
            # Reply не на вопрос-сообщение (или его не разобрать) — не теряем текст.
            clean = await _accept_user_text(message, text)
            if clean is not None:
                await _ingest_note(
                    message, clean,
                    note_prefix="Не понял, на какой вопрос это ответ — сохранил как заметку",
                )
            return
        # Цель == текущий активный неотвеченный вопрос → это обычный ответ,
        # сессию зря не пересоздаём.
        is_current = (
            s is not None and s.mode == "probe"
            and s.current_q_num == entry["q_num"] and not entry.get("answered")
        )
        if not is_current:
            _open_anchored_session(entry)
            s = session.get()

    if s is None:
        # Нет активной сессии и это не reply на вопрос — не теряем текст: в заметку.
        clean = await _accept_user_text(message, text)
        if clean is not None:
            await _ingest_note(
                message, clean,
                note_prefix="Сейчас сессии нет — сохранил как заметку (/ask — начать диалог)",
            )
        return

    # probe
    if not s.last_question:
        await message.answer("Сначала вопрос. Напиши /ask.")
        return

    # Per-user single-flight + cooldown: один LLM-вызов на пользователя за раз.
    uid = userctx.current_uid()
    if not ratelimit.try_acquire(uid):
        await message.answer(ratelimit.BUSY_MESSAGE)
        return
    try:
        await _handle_probe(message, text)
        # Явная фиксация после каждого ответа пользователя (захватывает финальное
        # состояние сессии поверх коммитов git_wrap внутри _apply_processed).
        vault.commit_all("answer")
    finally:
        ratelimit.release(uid)


async def _handle_probe(message: Message, text: str) -> None:
    # Сериализуем ответы одного пользователя: probe НЕ проходит single-flight
    # ratelimit (он только у /ask, /about, /ucho), поэтому два быстрых сообщения
    # иначе переплелись бы на await-границах (classify_mood/process_answer) и
    # испортили бы порядок history/mood_trajectory.
    async with session.lock_for(userctx.current_uid()):
        await _handle_probe_locked(message, text)


async def _handle_probe_locked(message: Message, text: str) -> None:
    s = session.get()
    if s is None:
        return
    clean = await _accept_user_text(message, text)
    if clean is None:
        return
    try:
        payload = await conversation_service.process_probe_answer(
            clean,
            message_id=getattr(message, "message_id", None),
            at=getattr(message, "date", None),
            reply_to_message_id=(
                message.reply_to_message.message_id if message.reply_to_message is not None else None
            ),
            is_owner=_is_owner(message),
        )
    except LLMError as exc:
        # Ожидаемый сбой модели → нейтральная фраза, pending_answer остаётся —
        # recovery дожмёт на следующем старте. Прочие исключения (баги) не
        # глушим: уходят в глобальный @dp.errors().
        log.warning("process_answer LLM error")
        await message.answer(
            _llm_user_message(exc, "Не получилось разобрать ответ.")
            + " Ответ оставлен в очереди обработки; можно повторить позже или /start (смыв)."
        )
        return
    if payload is None:
        return
    if payload.mood_message:
        await message.answer(payload.mood_message)
    await _send_question(
        message.bot, message.chat.id,
        q_num=payload.q_num, mode=payload.mode, domain=payload.domain, text=payload.text, plain=True,
        bot_mood=payload.bot_mood,
        admin_controls=_is_owner(message) and bool(payload.bot_mood),
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


# ---------- scheduler / ask helpers ----------


async def _send_next_question(
    bot: Bot,
    chat_id: int,
    domain: str | None = None,
    *,
    hint: str | None = None,
    show_thinking: bool = False,
) -> None:
    s = session.get()
    if s is None:
        s = session.start(mode="probe", domain=domain)

    # Если домен не указан — выбираем случайно из 10 (равномерно). Это даёт честное
    # покрытие всех тем вместо того, чтобы LLM сам выбирал любимые домены при
    # domain=any. Но при наличии hint домен НЕ форсируем: LLM подберёт его под
    # затравку человека (иначе случайный домен противоречил бы запросу).
    if domain is None and hint is None:
        domain = random.choice(DOMAINS)
        log.info("random domain selected for main question: %s", domain)

    # Индикатор «Думаю» (🎰 + текст) — только для /ask (show_thinking=True).
    # Дневной вопрос молчит: лишь нативное «печатает…», ровно одно сообщение-вопрос.
    thinking = None
    if show_thinking:
        thinking = await _start_thinking_chat(bot, chat_id)
    else:
        try:
            await bot.send_chat_action(chat_id, "typing")
        except Exception:
            pass

    # Лицо для вопроса: вектор настроения из сессии (или prior из портрета, если
    # сессия свежая) → контрастное лицо. Сбой не мешает задать вопрос.
    bot_mood = None
    try:
        mv = moods.session_mood(getattr(s, "mood_trajectory", []) or [], mood_file.baseline())
        bot_mood = moods.pick_bot_mood(mv)
    except Exception:
        log.exception("ask mood pick failed (non-fatal)")

    try:
        # Главный вопрос (/ask, дневной) генерим НЕ опираясь на текущую базу:
        # без context_concepts и recent_raw — свежий, «случайный» вопрос по теме,
        # а не вытекающий из уже зафиксированного. Так разговор не зацикливается
        # на накопленном графе.
        result = await ask_next(
            domain=_real_domain(domain),
            context_concepts="",
            recent_raw="",
            hint=hint,
            bot_mood=bot_mood,
            mode=s.mode,
        )
    except LLMError as exc:
        # Ожидаемый сбой модели → нейтральная фраза. Прочие исключения (баги)
        # не глушим: уходят в глобальный @dp.errors().
        log.warning("ask_next LLM error")
        await _stop_thinking(thinking)
        await bot.send_message(
            chat_id,
            _llm_user_message(exc, "Не вышло сформулировать вопрос. Попробуй ещё раз."),
        )
        session.clear()
        return
    await _stop_thinking(thinking)

    q_num = vault.next_q_num()
    session.set_question(_question_field_with_face(result["question"], bot_mood), result["domain"], q_num=q_num)
    # Новый главный вопрос — сбрасываем счётчик поясняющих и запоминаем главный.
    s.main_question = result["question"]
    s.main_q_num = q_num
    s.clarifier_count = 0
    session.persist()
    await _send_question(
        bot, chat_id,
        q_num=q_num, mode=s.mode, domain=result["domain"], text=result["question"],
        bot_mood=bot_mood,
    )


async def send_daily_question(bot: Bot, uid: int) -> bool:
    return await daily_service.send_daily_question(bot, uid)
