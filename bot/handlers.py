import html
import logging
import random
from datetime import datetime, timedelta

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from . import graph, session, vault
from .config import DOMAINS, OPENAI_MODEL, OWNER_TELEGRAM_ID, VAULT_PATH
from .graph import Concept, Evidence, RELATION_KINDS
from .llm import ask_next, ping_llm, process_answer, review_query, summarize_session

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

# В режиме probe — главный вопрос плюс не более N поясняющих, затем закрывающий комментарий.
MAX_CLARIFIERS = 1


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
    return message.from_user is not None and message.from_user.id == OWNER_TELEGRAM_ID


def _format_q(q_num: int, mode: str, domain: str, question_text: str) -> str:
    """Сформировать HTML-сообщение с вопросом.

    Заголовок: Q42 · [mode ·] <i>domain</i>
    Тело: <code>…</code> — inline-моноширинный шрифт. Telegram также даёт
    long-press «копировать» на <code>.
    Отправлять с parse_mode='HTML'.
    """
    label = USER_DOMAIN_LABEL if domain == USER_DOMAIN else domain
    parts = [f"Q{q_num}"]
    if mode and mode != "probe":
        parts.append(html.escape(mode))
    parts.append(f"<i>{html.escape(label)}</i>")
    head = " · ".join(parts)
    body = html.escape(question_text)
    return f"{head}\n\n<code>{body}</code>"


def _real_domain(d: str | None) -> str | None:
    """Возвращает d только если это валидный концептный домен. Иначе None."""
    return d if d in DOMAINS else None


# ---------- thinking / spinner ----------

_THINKING_EMOJIS = ("🎰", "🎲", "🎯")


def _thinking_token() -> str:
    return random.choice(_THINKING_EMOJIS)


async def _start_thinking(message: Message, text: str | None = None) -> Message | None:
    """Послать индикатор «думаю».

    Гибрид: dice-стикер (анимация, Telegram запрещает его удалять) + текстовый
    placeholder «…» (его удалим, когда ответ готов). Возвращает Message текстового
    placeholder для последующего удаления — dice остаётся в чате как маркер
    «здесь был ход размышления».
    """
    emoji = text if text in _THINKING_EMOJIS else _thinking_token()
    try:
        await message.bot.send_chat_action(message.chat.id, "typing")
    except Exception:
        pass
    # 1. Анимированный dice — sendDice. Удалить нельзя, но анимация играет.
    try:
        await message.answer_dice(emoji=emoji)
    except Exception:
        log.exception("failed to send dice indicator")
    # 2. Удаляемый текстовый placeholder.
    try:
        return await message.answer("Думаю.")
    except Exception:
        log.exception("failed to send thinking placeholder")
        return None


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
            f = vault.RAW_DIR / f"{day.isoformat()}.md"
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


def _catalog_text(max_chars: int = 12000) -> str:
    items = graph.all_slugs()
    if not items:
        return "(база пуста)"
    lines = [f"- [{x['domain']}] {x['slug']} ({x['name']}): {x['summary']}" for x in items]
    text = "\n".join(lines)
    return text[:max_chars]


def _apply_processed(
    result: dict,
    q_num: int,
    asked_at: datetime,
    original_question: str,
    original_answer: str,
) -> None:
    """Записать в raw и применить изменения к графу."""
    raw_entry = result.get("raw_entry") or {}
    raw_domain = raw_entry.get("domain") or "everyday"
    raw_fragment = raw_entry.get("fragment") or original_answer[:200]

    vault.append_raw(
        q_num=q_num,
        when=asked_at,
        domain=raw_domain,
        question=original_question,
        answer=original_answer,
    )
    vault.append_profile(
        when=datetime.now(),
        domain=raw_domain,
        fragment=raw_fragment,
        raw_time=asked_at.strftime("%H:%M"),
    )

    raw_ref = f"[[raw/{asked_at.strftime('%Y-%m-%d')}|Q{q_num}]]"
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")

    # 1. Создать новые концепты (со связями)
    for c_data in result.get("concepts_to_create", []):
        try:
            concept = Concept(
                slug=c_data["slug"],
                name=c_data.get("name", c_data["slug"]),
                type=c_data.get("type", "claim"),
                domain=c_data.get("domain", raw_domain),
                summary=c_data.get("summary", ""),
                status="tentative",
                evidence=[Evidence(when=now_str, text=c_data.get("evidence", original_answer[:200]), raw_ref=raw_ref)],
            )
            graph.save_concept(concept)
            for rel in c_data.get("relations") or []:
                kind = rel.get("kind", "related")
                to_slug = rel.get("to")
                if kind in RELATION_KINDS and to_slug:
                    graph.add_relation(concept.slug, to_slug, kind)
        except Exception:
            log.exception("failed to create concept from %r", c_data)

    # 2. Обновить существующие
    for u in result.get("concepts_to_update", []):
        slug = u.get("slug")
        if not slug:
            continue
        try:
            ev_text = u.get("append_evidence")
            if ev_text:
                graph.append_evidence(slug, Evidence(when=now_str, text=ev_text, raw_ref=raw_ref))
            summary_patch = u.get("summary_patch")
            if summary_patch:
                graph.patch_summary(slug, summary_patch)
        except Exception:
            log.exception("failed to update concept %s", slug)

    # 3. Дополнительные связи
    for r in result.get("relations_to_add", []):
        try:
            graph.add_relation(r["from"], r["to"], r["kind"], note=r.get("note"))
        except Exception:
            log.exception("failed to add relation %r", r)

    # 4. Конфликты: записать пробинг в оба концепта
    for conf in result.get("conflicts", []):
        a, b, probe = conf.get("concept_a"), conf.get("concept_b"), conf.get("probe")
        if not (a and b and probe):
            continue
        try:
            graph.add_relation(a, b, "contradicts")
            graph.append_open_question(a, f"vs [[{b}]]: {probe}")
            graph.append_open_question(b, f"vs [[{a}]]: {probe}")
        except Exception:
            log.exception("failed to record conflict %s/%s", a, b)


# ---------- commands ----------


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    if not _is_owner(message):
        log.info("ignored /start from non-owner user_id=%s", message.from_user.id if message.from_user else None)
        return
    vault.ensure_layout()
    await message.answer(
        "Привет. Я твой собеседник для портрета.\n\n"
        "• /ask [domain] — задам вопрос, разберу ответ остро и углублю.\n"
        "• /requestion <текст> — твой собственный вопрос, обработаю как обычный.\n"
        "• /discuss [concept|domain] — выступлю оппонентом по существующей позиции.\n"
        "• /review — поговорим про твою базу знаний, найду противоречия.\n"
        "• /history — все вопросы и ответы.\n"
        "• /retry N — задать заново вопрос с номером N.\n"
        "• /answer N <текст> — ответить прямо на Q<N>, минуя сессию.\n"
        "• /ping — проверка, что бот и LLM живы.\n"
        "• /end — закрыть текущую сессию.\n\n"
        f"Доступные домены: {', '.join(DOMAINS)}.\n"
        "Раз в день сам пришлю вопрос. Граф концептов растёт в Obsidian → Graph View, фильтр `path:concepts/`."
    )


@router.message(Command("end", "skip"))
async def cmd_end(message: Message) -> None:
    if not _is_owner(message):
        return
    if session.get() is None:
        await message.answer("Сейчас сессии нет — нечего закрывать.")
        return
    session.clear()
    await message.answer("Ок, закрыл сессию.")


@router.message(Command("ask"))
async def cmd_ask(message: Message, command: CommandObject) -> None:
    if not _is_owner(message):
        return
    domain = (command.args or "").strip().lower() or None
    if domain and domain not in DOMAINS:
        await message.answer(f"Не знаю домен «{domain}». Доступны: {', '.join(DOMAINS)}.")
        return
    if domain is None:
        await message.answer("Выбери домен:", reply_markup=_ask_keyboard())
        return
    session.start(mode="probe", domain=domain)
    await _send_next_question(message.bot, message.chat.id, domain=domain)


@router.callback_query(F.data.startswith("ask:"))
async def cb_ask_domain(callback: CallbackQuery) -> None:
    if callback.from_user.id != OWNER_TELEGRAM_ID:
        await callback.answer()
        return
    payload = (callback.data or "").split(":", 1)[1]
    domain = None if payload == "any" else payload
    if domain is not None and domain not in DOMAINS:
        await callback.answer("Неизвестный домен", show_alert=True)
        return
    if callback.message:
        try:
            label = _DOMAIN_LABELS.get(domain or "", "🎲 на выбор бота")
            await callback.message.edit_text(f"Домен: {label}")
        except Exception:
            log.exception("failed to clear ask keyboard")
    await callback.answer()
    session.start(mode="probe", domain=domain)
    chat_id = callback.message.chat.id if callback.message else callback.from_user.id
    await _send_next_question(callback.bot, chat_id, domain=domain)


@router.message(Command("discuss"))
async def cmd_discuss(message: Message, command: CommandObject) -> None:
    if not _is_owner(message):
        return
    arg = (command.args or "").strip().lower() or None

    target_slug = None
    target_domain = None
    if arg:
        if arg in DOMAINS:
            target_domain = arg
        else:
            target_slug = arg
            c = graph.load_concept(target_slug)
            if c is None:
                await message.answer(f"Концепт `{target_slug}` не найден. Доступные домены: {', '.join(DOMAINS)}.")
                return
            target_domain = c.domain

    session.start(mode="discuss", domain=target_domain)
    await _send_next_question(message.bot, message.chat.id, domain=target_domain, slug_hint=target_slug)


@router.message(Command("review"))
async def cmd_review(message: Message) -> None:
    if not _is_owner(message):
        return
    session.start(mode="review")
    await message.answer(
        "Режим /review. Спрашивай по своей базе — найду концепты, обсудим связи и противоречия.\n"
        "Закрыть: /end."
    )


@router.message(Command("history"))
async def cmd_history(message: Message) -> None:
    if not _is_owner(message):
        return
    entries = vault.iter_history()
    if not entries:
        await message.answer("История пуста. /ask чтобы начать.")
        return
    lines = [f"📜 История: {len(entries)} вопрос(ов).", ""]
    for e in entries:
        q = e["question"]
        a = e["answer"]
        if len(q) > 180:
            q = q[:180].rstrip() + "…"
        if len(a) > 180:
            a = a[:180].rstrip() + "…"
        lines.append(f"*Q{e['n']}* · {e['date']} {e['time']} · {e['domain']}")
        lines.append(f"❓ {q}")
        lines.append(f"💬 {a}")
        lines.append("")
    lines.append("Перепрожить вопрос: /retry N")
    text = "\n".join(lines)
    for chunk in _split_for_telegram(text):
        await message.answer(chunk)


@router.message(Command("retry"))
async def cmd_retry(message: Message, command: CommandObject) -> None:
    if not _is_owner(message):
        return
    arg = (command.args or "").strip()
    try:
        n = int(arg)
    except ValueError:
        await message.answer("Использование: /retry <номер>. Список: /history.")
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
    session.set_question(entry["question"], entry["domain"], q_num=new_n)
    s.main_question = entry["question"]
    s.main_q_num = new_n
    s.clarifier_count = 0
    session.persist()
    s.record_assistant(entry["question"])
    msg = _format_q(new_n, "probe", entry["domain"], entry["question"])
    msg += f"\n<i>(повтор Q{n})</i>"
    await message.answer(msg, parse_mode="HTML")


@router.message(Command("requestion"))
async def cmd_requestion(message: Message, command: CommandObject) -> None:
    """Пользователь сам задаёт вопрос. Бот дублирует его как Q под меткой «пользовательский»."""
    if not _is_owner(message):
        return
    text = (command.args or "").strip()
    if not text:
        await message.answer("Использование: /requestion <текст твоего вопроса>")
        return

    new_n = vault.next_q_num()
    session.start(mode="probe", domain=None)
    s = session.get()
    if s is None:
        return
    session.set_question(text, USER_DOMAIN, q_num=new_n)
    s.main_question = text
    s.main_q_num = new_n
    s.clarifier_count = 0
    session.persist()
    s.record_assistant(text)
    await message.answer(_format_q(new_n, "probe", USER_DOMAIN, text), parse_mode="HTML")


@router.message(Command("ping"))
async def cmd_ping(message: Message) -> None:
    """Проверка живости бота + LLM round-trip + статус сессии."""
    if not _is_owner(message):
        return
    thinking = await _start_thinking(message)
    ok, latency, err = await ping_llm()
    await _stop_thinking(thinking)

    s = session.get()
    if s is None:
        session_info = "нет"
    else:
        session_info = f"{s.mode}"
        if s.current_q_num is not None:
            session_info += f" · Q{s.current_q_num}"
        if s.last_domain:
            label = USER_DOMAIN_LABEL if s.last_domain == USER_DOMAIN else s.last_domain
            session_info += f" · {label}"

    llm_line = f"LLM: {'✅' if ok else '❌'} {OPENAI_MODEL}"
    if latency is not None:
        llm_line += f" · {latency:.1f}s"
    if err:
        llm_line += f"\n  └ {err[:200]}"

    lines = [
        "🟢 pong",
        "",
        llm_line,
        f"session: {session_info}",
        f"vault: {VAULT_PATH}",
    ]
    await message.answer("\n".join(lines))


@router.message(Command("answer"))
async def cmd_answer(message: Message, command: CommandObject) -> None:
    """Ответить прямо на Q<N>, минуя активную сессию.

    /answer 7 мой текст ответа — найдёт Q7 (в активной сессии или в истории) и
    обработает текст как ответ на этот вопрос. Полезно, если бот рестартовал, а
    ответ хочется дать ровно на тот вопрос.
    """
    if not _is_owner(message):
        return
    arg = (command.args or "").strip()
    if not arg:
        await message.answer("Использование: /answer N <текст ответа>. Список вопросов: /history.")
        return
    parts = arg.split(maxsplit=1)
    try:
        n = int(parts[0])
    except ValueError:
        await message.answer("Первый аргумент — номер вопроса. Пример: /answer 7 мой ответ.")
        return
    if len(parts) < 2 or not parts[1].strip():
        await message.answer("После номера нужен текст ответа. Пример: /answer 7 мой ответ.")
        return
    answer_text = parts[1].strip()

    s = session.get()

    # Случай 1: активная сессия и Q совпадает с last_question — обычный path.
    if s is not None and s.current_q_num == n and s.last_question:
        await _handle_probe_or_discuss(message, answer_text)
        return

    # Случай 2: Q<N> уже есть в истории — это «доп. ответ» на старый вопрос.
    entry = vault.find_question(n)
    if entry is None:
        # Если сессия активна и last_question есть — мог быть имеется в виду свежий Q,
        # но номер не сошёлся: подскажем.
        if s is not None and s.last_question:
            await message.answer(
                f"Q{n} не найден. Текущий незакрытый вопрос: Q{s.current_q_num}. "
                "Чтобы ответить на него — просто пиши текстом или /answer N с правильным номером."
            )
        else:
            await message.answer(f"Q{n} не найден ни в активной сессии, ни в истории. /history покажет, что есть.")
        return

    new_n = vault.next_q_num()
    asked_at = datetime.now()
    domain = entry["domain"]
    question_text = entry["question"]

    context_concepts = _context_for_domain(_real_domain(domain))
    thinking = await _start_thinking(message)
    try:
        result = await process_answer(
            question=question_text,
            answer=answer_text,
            domain_hint=_real_domain(domain),
            context_concepts=context_concepts,
            history=None,
            mode="probe",
        )
    except Exception:
        log.exception("process_answer failed in /answer")
        await _stop_thinking(thinking)
        await message.answer("Не получилось разобрать ответ. Попробуй ещё раз позже.")
        return
    await _stop_thinking(thinking)

    try:
        _apply_processed(result, new_n, asked_at, question_text, answer_text)
    except Exception:
        log.exception("apply_processed failed in /answer")

    debate = (result.get("debate_message") or "").strip()
    head = f"Записал как Q{new_n} (доп. ответ на Q{n} · {domain})."
    if debate:
        await message.answer(f"{head}\n\n{debate}")
    else:
        await message.answer(head)


# ---------- text messages ----------


@router.message(F.text)
async def on_text(message: Message) -> None:
    if not _is_owner(message):
        return
    s = session.get()
    text = (message.text or "").strip()

    if s is None:
        await message.answer("Сейчас сессии нет. /ask чтобы начать, /review чтобы поговорить о базе.")
        return

    # /review: подтверждение предложенных добавлений
    if s.mode == "review" and s.pending_review_additions and text.lower() in {"да", "yes", "ок", "+"}:
        await _commit_review_additions(message)
        return
    if s.mode == "review" and s.pending_review_additions and text.lower() in {"нет", "no", "-"}:
        s.pending_review_additions = []
        session.persist()
        await message.answer("Ок, не записал.")
        return

    if s.mode == "review":
        await _handle_review(message, text)
        return

    # probe / discuss
    if not s.last_question:
        await message.answer("Сначала вопрос. Напиши /ask или /discuss.")
        return

    await _handle_probe_or_discuss(message, text)


async def _handle_probe_or_discuss(message: Message, text: str) -> None:
    s = session.get()
    if s is None:
        return
    if s.current_q_num is None:
        log.warning("session has no current_q_num; assigning fresh")
        s.current_q_num = vault.next_q_num()
        session.persist()
    s.record_user(text)
    real_hint = _real_domain(s.last_domain) or _real_domain(s.domain)
    context_concepts = _context_for_domain(real_hint)

    thinking = await _start_thinking(message)
    try:
        result = await process_answer(
            question=s.last_question,
            answer=text,
            domain_hint=real_hint,
            context_concepts=context_concepts,
            history=s.history[:-1],
            mode=s.mode,
        )
    except Exception:
        log.exception("process_answer failed")
        await _stop_thinking(thinking)
        await message.answer("Не получилось разобрать ответ. Сформулируй ещё раз или /end.")
        return
    await _stop_thinking(thinking)

    try:
        _apply_processed(result, s.current_q_num, s.asked_at, s.last_question, text)
    except Exception:
        log.exception("apply_processed failed")

    debate = (result.get("debate_message") or "").strip()
    close = bool(result.get("close_session"))

    # /discuss закрывается только по команде пользователя.
    if s.mode == "discuss":
        close = False

    # В режиме probe лимит поясняющих: после MAX_CLARIFIERS отвеченных
    # пояснений — закрывающий комментарий вместо нового вопроса.
    force_close_with_summary = (
        s.mode == "probe" and s.clarifier_count >= MAX_CLARIFIERS
    )

    if force_close_with_summary or (s.mode == "probe" and close):
        thinking2 = await _start_thinking(message)
        try:
            comment = await summarize_session(
                main_question=s.main_question or s.last_question,
                exchanges=list(s.history),
            )
        except Exception:
            log.exception("summarize_session failed")
            comment = "Сессия закрыта."
        await _stop_thinking(thinking2)
        if comment:
            await message.answer(comment)
        session.clear()
        return

    if close:
        # discuss-ветка с close=False сюда не попадает; этот блок для будущей совместимости.
        if debate:
            await message.answer(debate)
        await message.answer("Закрыл сессию.")
        session.clear()
        return

    if not debate:
        debate = "Понял, продолжим — что скажешь дальше?"

    # Новый поясняющий вопрос — счётчик +1
    new_n = vault.next_q_num()
    s.record_assistant(debate)
    next_domain = (result.get("raw_entry") or {}).get("domain") or s.last_domain or "everyday"
    session.set_question(debate, next_domain, q_num=new_n)
    if s.mode == "probe":
        s.clarifier_count += 1
        session.persist()
    await message.answer(_format_q(new_n, s.mode, next_domain, debate), parse_mode="HTML")


async def _handle_review(message: Message, text: str) -> None:
    s = session.get()
    if s is None:
        return
    s.record_user(text)
    thinking = await _start_thinking(message)
    try:
        result = await review_query(
            query=text,
            catalog=_catalog_text(),
            history=s.history[:-1],
        )
    except Exception:
        log.exception("review_query failed")
        await _stop_thinking(thinking)
        await message.answer("Не получилось обработать запрос. Переформулируй или /end.")
        return
    await _stop_thinking(thinking)

    answer = (result.get("answer") or "").strip() or "Не нашёл связного ответа в базе."
    s.record_assistant(answer)
    for chunk in _split_for_telegram(answer):
        await message.answer(chunk)

    additions = result.get("suggested_additions") or []
    if additions:
        s.pending_review_additions = additions
        session.persist()
        names = ", ".join(a.get("name") or a.get("slug") or "?" for a in additions)
        await message.answer(f"Заметил новое: {names}.\nДобавить в базу? `да` / `нет`.")


async def _commit_review_additions(message: Message) -> None:
    s = session.get()
    if s is None or not s.pending_review_additions:
        return
    asked_at = datetime.now()
    raw_ref = f"[[raw/{asked_at.strftime('%Y-%m-%d')}|review]]"
    now_str = asked_at.strftime("%Y-%m-%d %H:%M")
    added = 0
    for a in s.pending_review_additions:
        try:
            concept = Concept(
                slug=a["slug"],
                name=a.get("name", a["slug"]),
                type=a.get("type", "claim"),
                domain=a.get("domain", "everyday"),
                summary=a.get("summary", ""),
                status="tentative",
                evidence=[Evidence(when=now_str, text=a.get("evidence", ""), raw_ref=raw_ref)],
            )
            graph.save_concept(concept)
            added += 1
        except Exception:
            log.exception("failed to add concept from review: %r", a)
    s.pending_review_additions = []
    session.persist()
    await message.answer(f"Записал {added} концептов.")


# ---------- scheduler / ask helpers ----------


async def _send_next_question(
    bot: Bot,
    chat_id: int,
    domain: str | None = None,
    slug_hint: str | None = None,
) -> None:
    s = session.get()
    if s is None:
        s = session.start(mode="probe", domain=domain)

    # Если домен не указан — выбираем случайно из 10 (равномерно).
    # Это даёт честное покрытие всех тем вместо того, чтобы LLM сам выбирал
    # любимые домены при mode="ask domain=any".
    if domain is None and not slug_hint:
        domain = random.choice(DOMAINS)
        log.info("random domain selected for main question: %s", domain)

    if slug_hint:
        concepts = graph.find_concepts(domain=domain, slugs=[slug_hint], limit=10)
        if not concepts:
            concepts = graph.find_concepts(domain=domain, limit=30)
        context_concepts = graph.context_snapshot(concepts)
    else:
        context_concepts = _context_for_domain(domain)

    try:
        await bot.send_chat_action(chat_id, "typing")
    except Exception:
        pass
    thinking = None
    try:
        try:
            await bot.send_dice(chat_id, emoji=_thinking_token())
        except Exception:
            log.exception("failed to send dice in _send_next_question")
        thinking = await bot.send_message(chat_id, "Думаю.")
    except Exception:
        log.exception("failed to send thinking message")

    try:
        result = await ask_next(
            domain=_real_domain(domain),
            context_concepts=context_concepts,
            recent_raw=_recent_raw_text(),
            mode=s.mode,
        )
    except Exception:
        log.exception("ask_next failed")
        if thinking is not None:
            try:
                await thinking.delete()
            except Exception:
                pass
        await bot.send_message(chat_id, "Не вышло сформулировать вопрос. Попробуй ещё раз.")
        session.clear()
        return

    if thinking is not None:
        try:
            await thinking.delete()
        except Exception:
            pass

    q_num = vault.next_q_num()
    session.set_question(result["question"], result["domain"], q_num=q_num)
    # Новый главный вопрос — сбрасываем счётчик поясняющих и запоминаем главный.
    s.main_question = result["question"]
    s.main_q_num = q_num
    s.clarifier_count = 0
    session.persist()
    s.record_assistant(result["question"])
    await bot.send_message(
        chat_id,
        _format_q(q_num, s.mode, result["domain"], result["question"]),
        parse_mode="HTML",
    )


async def send_daily_question(bot: Bot) -> None:
    """Точка входа для scheduler-а."""
    if session.get() is not None:
        log.info("daily skipped: session already active")
        return
    session.start(mode="probe", domain=None)
    await _send_next_question(bot, OWNER_TELEGRAM_ID, domain=None)
