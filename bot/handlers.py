import html
import logging
import random
import re
from datetime import datetime, timedelta

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from . import about, graph, moods, qmap, questions, ratelimit, session, sessions, translate, userctx, users, vault
from .config import ALLOWED_TELEGRAM_IDS, DAILY_TZ, DOMAINS, OWNER_TELEGRAM_ID
from .llm import about_present, ask_next, classify_mood, process_answer
from .services.answer_service import apply_processed
from .validation import (
    MAX_USER_TEXT,
    is_valid_telegram_command_arg,
    safe_chat_html,
    safe_user_text,
)

log = logging.getLogger(__name__)
router = Router()

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

TG_MSG_LIMIT = 4000  # запас от 4096

# Сентинел для домена, помеченного пользователем (/requestion). В DOMAINS его нет —
# он влияет только на отображение «пользовательский» в сообщении бота. LLM на этот
# домен не получает хинт, чтобы он сам выбрал реальный домен для концептов.
USER_DOMAIN = "user"
USER_DOMAIN_LABEL = "пользовательский"

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


# ---------- helpers ----------


def _is_owner(message: Message) -> bool:
    return message.from_user is not None and users.is_owner(message.from_user.id)


def _is_allowed(message: Message) -> bool:
    return message.from_user is not None and users.is_allowed(message.from_user.id)


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
    label = USER_DOMAIN_LABEL if domain == USER_DOMAIN else domain
    parts = [f"Q{q_num}"]
    if mode and mode != "probe":
        parts.append(html.escape(mode))
    parts.append(f"<i>{html.escape(label)}</i>")
    head = " · ".join(parts)
    safe_q = question_text or ""
    if len(safe_q) > 3500:
        safe_q = safe_q[:3500].rstrip() + "…"
    body = html.escape(safe_q)
    return f"{head}\n\n<code>{body}</code>"


def _real_domain(d: str | None) -> str | None:
    """Возвращает d только если это валидный концептный домен. Иначе None."""
    return d if d in DOMAINS else None


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
) -> Message | None:
    """Отправить сообщение сессии И записать его в qmap + в message_ids сессии.

    Единая точка отправки реплики бота в обсуждении (главный вопрос, реакция,
    /echo, /requestion, recovery): гарантирует, что message_id попадает в qmap
    (для `/answer N` и реконструкции) и в `message_ids` активной сессии (для
    reply-resume). ``plain=True`` — реплика-реакция: без заголовка «Q<n> · domain»,
    просто речь от первого лица.
    """
    if plain:
        # Реакция/реплика от LLM: экранируем и нормализуем — любой «код»/разметка
        # от модели уходит как обычный текст, Telegram его не интерпретирует.
        body = safe_chat_html(text)
    else:
        body = _format_q(q_num, mode, domain, text)
    if suffix:
        body += suffix
    sent = await bot.send_message(chat_id, body, parse_mode="HTML")
    try:
        qmap.append(sent.message_id, q_num, text, domain)
    except Exception:
        log.exception("failed to record question in qmap (q_num=%s)", q_num)
    # Главный вопрос (не реакция) → в кольцо вопросов для /history.
    if not plain:
        questions.record(q_num, domain, text)
    s = session.get()
    if s is not None:
        try:
            s.add_message_id(sent.message_id)
        except Exception:
            log.exception("failed to record message_id in session")
    return sent


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
    s.record_assistant(text)


def _anchor_user_cmd(message: Message) -> None:
    """Записать сообщение-команду пользователя как первое в активной сессии.

    Команда открывает сессию-обсуждение — её первое сообщение это команда
    пользователя. Нужно, чтобы reply на любое сообщение этой сессии (в т.ч. на
    саму команду) её резюмировал (см. reply-resume / `bot/sessions.py`).
    """
    s = session.get()
    mid = getattr(message, "message_id", None)
    if s is not None and mid:
        s.add_message_id(int(mid))


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
        s.record_assistant(a)
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
    if len(text) <= TG_MSG_LIMIT:
        return [text]
    parts: list[str] = []
    buf: list[str] = []
    buf_len = 0
    for line in text.split("\n"):
        add = len(line) + 1
        if buf_len + add > TG_MSG_LIMIT and buf:
            parts.append("\n".join(buf))
            buf = [line]
            buf_len = add
        else:
            buf.append(line)
            buf_len += add
    if buf:
        parts.append("\n".join(buf))
    return parts


def _recent_raw_text(days: int = 7, max_chars: int = 8000) -> str:
    try:
        chunks: list[str] = []
        today = datetime.now().date()
        for delta in range(days):
            day = today - timedelta(days=delta)
            f = vault.raw_dir() / f"{day.isoformat()}.md"
            if f.exists():
                chunks.append(f.read_text(encoding="utf-8"))
                if sum(len(c) for c in chunks) > max_chars:
                    break
        return "\n\n".join(chunks)[:max_chars]
    except Exception:
        log.exception("failed to load recent raw")
        return ""


def _context_for_domain(domain: str | None) -> str:
    concepts = graph.find_concepts(domain=domain, limit=30)
    return graph.context_snapshot(concepts)


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
    "<b>/dailyall</b> — разослать дневной вопрос всем прямо сейчас"
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


@router.message(Command("adduser"))
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


@router.message(Command("removeuser"))
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


@router.message(Command("users"))
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


@router.message(Command("dailyall"))
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
    s.record_assistant(entry["question"])
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
    s.record_assistant(text)
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
    """Показать пользователю его портрет (about_user.md) — отформатированный
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
    except Exception:
        log.exception("about_present failed")
        await _stop_thinking(thinking)
        await message.answer("Не вышло собрать портрет словами. Попробуй позже.")
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
        # Заметка тоже открывает сессию-обсуждение (как /ucho): первое сообщение —
        # сообщение пользователя; на reply/продолжение пойдёт обычная реакция.
        session.start(mode="probe", domain=None)
        _anchor_user_cmd(message)
        # 1. Verbatim в notes/ (человеческий скрэтчпад).
        when = datetime.now()
        try:
            vault.append_note(when, clean)
        except Exception:
            log.exception("failed to append note")

        # 2. Прогон через LLM process. Синтетический «вопрос» — заметка свободная,
        #    домен выберет LLM. Концепты привязываются к raw-блоку (для evidence).
        q_num = vault.next_q_num()
        note_question = "(свободная заметка)"
        context_concepts = _context_for_domain(None)
        # /ucho — без индикатора «Думаю» (он только для /ask и /about).
        try:
            result = await process_answer(
                question=note_question,
                answer=clean,
                domain_hint=None,
                context_concepts=context_concepts,
                history=None,
                mode="probe",
            )
        except Exception:
            log.exception("process_answer failed in note ingest")
            prefix = (note_prefix + ". ") if note_prefix else ""
            await _session_reply(
                message,
                f"{prefix}Заметку сохранил, но разобрать в граф не вышло. Попробуй позже.",
                anchor=clean,
            )
            return

        created = updated = 0
        try:
            created, updated = apply_processed(result, q_num, when, note_question, clean)
        except Exception:
            log.exception("apply_processed failed in note ingest")
        vault.commit_all(f"ucho note {when.strftime('%Y-%m-%d %H:%M')}")
        prefix = (note_prefix + ". ") if note_prefix else ""
        # Якорь обсуждения — текст заметки: продолжение пойдёт как ответ → реакция.
        await _session_reply(
            message,
            f"{prefix}Заметка сохранена (notes/{when.strftime('%Y-%m-%d')}.md). "
            f"В граф: +{created} новых, ~{updated} обновлено.",
            anchor=clean,
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
    s = session.get()
    if s is None:
        return
    clean = await _accept_user_text(message, text)
    if clean is None:
        return
    text = clean
    if s.current_q_num is None:
        log.warning("session has no current_q_num; assigning fresh")
        s.current_q_num = vault.next_q_num()
        session.persist()
    # Двухфазный коммит: сначала помечаем «есть необработанный ответ» на диске,
    # потом запускаем LLM-цепочку. Если бот упадёт посреди — на следующем
    # старте process_pending_on_startup дожмёт обработку.
    s.pending_answer = text
    session.persist()
    s.record_user(text)
    real_hint = _real_domain(s.last_domain) or _real_domain(s.domain)
    context_concepts = _context_for_domain(real_hint)

    # Настроение: классифицируем последнее сообщение → копим траекторию сессии →
    # считаем вектор (recency + затухающий prior из портрета) → выбираем КОНТРАСТНОЕ
    # лицо → пишем в портрет. Сбой не валит обработку ответа.
    mood_vec = None
    bot_mood = None
    vader = None
    try:
        # VADER — инструментальная подсказка тональности: переводим RU→EN локально
        # (Argos, офлайн) и считаем compound. LLM в classify_mood — арбитр.
        en = await translate.translate_ru_en(text)
        vader = moods.vader_compound(en)
        per_msg = await classify_mood(text, about.render_for_prompt(), vader=vader)
        s.record_mood(per_msg)
        mood_vec = moods.session_mood(s.mood_trajectory, about.baseline())
        bot_mood = moods.pick_bot_mood(mood_vec)
        about.set_mood(mood_vec, bot_mood)
    except Exception:
        log.exception("mood detection failed (non-fatal)")

    # Реакция считается молча (Q1: тишина) — без индикатора «Думаю».
    try:
        result = await process_answer(
            question=s.last_question,
            answer=text,
            domain_hint=real_hint,
            context_concepts=context_concepts,
            bot_mood=bot_mood,
            history=s.history[:-1],
            mode=s.mode,
        )
    except Exception:
        log.exception("process_answer failed")
        await message.answer("Не получилось разобрать ответ. Сформулируй ещё раз или /start (смыв).")
        return

    try:
        apply_processed(result, s.current_q_num, s.asked_at, s.last_question, text, session_domain=real_hint)
    except Exception:
        log.exception("apply_processed failed")

    # Ответ обработан (или провалился частично) — снимаем pending. Recovery
    # больше не должен пытаться повторить эту реплику, иначе задвоит граф.
    s.pending_answer = None
    session.persist()

    # Журнал пары (настроение → лицо) + vader для графа Фазы D (weekly агрегирует).
    if mood_vec and bot_mood:
        moods.log_turn(mood_vec, bot_mood, vader=vader)

    # Реакция вместо кларифера: реплика-укол от 1-го лица, НЕ вопрос. Сессия
    # НЕ закрывается — ждём следующего сообщения пользователя. Реакция
    # становится «якорем» следующего хода (её q_num, её message_id в сессии).
    reaction = (result.get("reaction") or "").strip() or "Складно. Слишком складно."
    s.record_assistant(reaction)
    new_n = vault.next_q_num()
    next_domain = s.last_domain if s.last_domain in DOMAINS else "everyday"
    session.set_question(reaction, next_domain, q_num=new_n)
    session.persist()
    await _send_question(
        message.bot, message.chat.id,
        q_num=new_n, mode=s.mode, domain=next_domain, text=reaction, plain=True,
    )


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

    real_hint = _real_domain(s.last_domain) or _real_domain(s.domain)
    context_concepts = _context_for_domain(real_hint)

    try:
        result = await process_answer(
            question=question,
            answer=text,
            domain_hint=real_hint,
            context_concepts=context_concepts,
            history=s.history[:-1] if s.history else None,
            mode=s.mode,
        )
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
    s.record_assistant(reaction)
    new_n = vault.next_q_num()
    next_domain = s.last_domain if s.last_domain in DOMAINS else "everyday"
    session.set_question(reaction, next_domain, q_num=new_n)
    session.persist()
    try:
        await _send_question(
            bot, uid,
            q_num=new_n, mode=s.mode, domain=next_domain, text=reaction, plain=True,
        )
    except Exception:
        log.exception("recovery: failed to send reaction")


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
        mv = moods.session_mood(getattr(s, "mood_trajectory", []) or [], about.baseline())
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
    except Exception:
        log.exception("ask_next failed")
        await _stop_thinking(thinking)
        await bot.send_message(chat_id, "Не вышло сформулировать вопрос. Попробуй ещё раз.")
        session.clear()
        return
    await _stop_thinking(thinking)

    q_num = vault.next_q_num()
    session.set_question(result["question"], result["domain"], q_num=q_num)
    # Новый главный вопрос — сбрасываем счётчик поясняющих и запоминаем главный.
    s.main_question = result["question"]
    s.main_q_num = q_num
    s.clarifier_count = 0
    session.persist()
    s.record_assistant(result["question"])
    await _send_question(
        bot, chat_id,
        q_num=q_num, mode=s.mode, domain=result["domain"], text=result["question"],
    )


async def send_daily_question(bot: Bot, uid: int) -> bool:
    """Дневной вопрос пользователю — раз в день. True если отправлен, False если
    пропущен (уже был сегодня).

    Дедуп по дате (`vault.daily_already_sent`/`mark_daily_sent`, общий маркер для
    cron, `/dailyall` и догона после простоя) — за день уходит ровно один. Активная
    сессия и прошлые ответы НЕ блокируют: дневной вопрос всё равно приходит (как
    `/ask`; текущая сессия уйдёт в кольцо через `session.start`).
    """
    userctx.set_user(uid)
    if vault.daily_already_sent(DAILY_TZ):
        log.info("daily skipped: already sent today uid=%s", uid)
        return False
    session.start(mode="probe", domain=None)
    await _send_next_question(bot, uid, domain=None)
    vault.mark_daily_sent(DAILY_TZ)
    return True


# ---------- офлайн-бэклог (сообщения, пришедшие пока бот лежал) ----------


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
            await _handle_probe(carrier, combined)
            vault.commit_all("offline batch")
        finally:
            ratelimit.release(uid)
    else:
        # Нет активной probe-сессии — склеить в заметку (тоже один итог).
        clean = await _accept_user_text(carrier, combined)
        if clean is not None:
            await _ingest_note(
                carrier, clean,
                note_prefix="Пока меня не было — склеил сообщения в заметку",
            )
