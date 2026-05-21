"""Обёртка над openai-совместимым API (Ollama).

Три функции под три mode из system-prompt:
- ask_next      → mode: ask
- process_answer → mode: process
- review_query  → mode: review
"""
import json
import logging
from typing import Optional

from openai import AsyncOpenAI

from . import vault
from .config import (
    DOMAINS,
    LLM_TIMEOUT,
    OPENAI_API_KEY,
    OPENAI_BASE_URL,
    OPENAI_MODEL,
    PROMPTS_DIR,
)

log = logging.getLogger(__name__)

# timeout — чтобы зависшая/упавшая Ollama не держала бота ~600 c (дефолт sdk).
# max_retries=1 — один повтор на транзиентный сбой, без многократного умножения
# ожидания (worst case ≈ 2 × LLM_TIMEOUT, а не 600 c).
_client = AsyncOpenAI(
    api_key=OPENAI_API_KEY,
    base_url=OPENAI_BASE_URL,
    timeout=LLM_TIMEOUT,
    max_retries=1,
)

_system_prompt = (PROMPTS_DIR / "system.md").read_text(encoding="utf-8")
_review_addendum = (PROMPTS_DIR / "review.md").read_text(encoding="utf-8")
_summarize_prompt = (PROMPTS_DIR / "summarize.md").read_text(encoding="utf-8")


def _system(mode: str) -> str:
    if mode == "review":
        return f"{_system_prompt}\n\n{_review_addendum}"
    return _system_prompt


async def _chat_json(messages: list[dict], temperature: float = 0.6) -> dict:
    """Вызов LLM с принудительным JSON-выводом."""
    resp = await _client.chat.completions.create(
        model=OPENAI_MODEL,
        response_format={"type": "json_object"},
        messages=messages,
        temperature=temperature,
    )
    raw = resp.choices[0].message.content or ""
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        log.error("LLM returned non-JSON: %r", raw[:500])
        raise


async def ask_next(
    domain: Optional[str] = None,
    context_concepts: str = "",
    recent_raw: str = "",
    history: Optional[list[dict]] = None,
    mode: str = "probe",
) -> dict:
    """Сгенерировать новый вопрос. Returns {'type', 'domain', 'question', 'targets_concept'}."""
    if domain and domain not in DOMAINS:
        raise ValueError(f"unknown domain: {domain}")

    user_msg = "\n\n".join(
        x for x in [
            "mode: ask",
            f"domain: {domain or 'any'}",
            f"context_concepts:\n{context_concepts or '(база пуста)'}",
            f"recent_raw:\n{recent_raw}" if recent_raw else "",
        ] if x
    )

    messages = [{"role": "system", "content": _system(mode)}]
    if history:
        messages.extend(history)
    messages.append({"role": "user", "content": user_msg})

    data = await _chat_json(messages, temperature=0.8)
    if "question" not in data or "domain" not in data:
        raise ValueError(f"malformed ask payload: {data}")
    if data["domain"] not in DOMAINS:
        bad = data["domain"]
        log.warning("LLM returned unknown domain %r, falling back to 'everyday'", bad)
        vault.append_log(
            "warn",
            "llm_domain_fallback",
            f"ask: LLM returned domain={bad!r} → coerced to 'everyday'",
        )
        data["domain"] = "everyday"
    return data


async def process_answer(
    question: str,
    answer: str,
    domain_hint: Optional[str],
    context_concepts: str = "",
    history: Optional[list[dict]] = None,
    mode: str = "probe",
) -> dict:
    """Разбор ответа пользователя.

    Returns dict с ключами:
        type, raw_entry, concepts_to_create, concepts_to_update,
        relations_to_add, conflicts, debate_message, close_session
    """
    user_msg = "\n\n".join([
        "mode: process",
        f"question: {question}",
        f"answer: {answer}",
        f"domain_hint: {domain_hint or 'any'}",
        f"context_concepts:\n{context_concepts or '(база пуста)'}",
    ])

    messages = [{"role": "system", "content": _system(mode)}]
    if history:
        messages.extend(history)
    messages.append({"role": "user", "content": user_msg})

    data = await _chat_json(messages, temperature=0.5)
    for key in ("concepts_to_create", "concepts_to_update", "relations_to_add", "conflicts"):
        data.setdefault(key, [])
    data.setdefault("debate_message", "")
    data.setdefault("close_session", False)
    data.setdefault("raw_entry", {"domain": domain_hint or "everyday", "fragment": answer[:200]})
    return data


async def summarize_session(main_question: str, exchanges: list[dict]) -> str:
    """Закрывающий комментарий после исчерпания клавиатуры вопросов.

    exchanges — это session.history (последние реплики user/assistant).
    Возвращает plain text без JSON.
    """
    closing_instruction = (
        f"Главный вопрос был: «{main_question}». "
        "Сессия закрывается — дай короткий комментарий о том, что прибавилось к портрету. "
        "3–5 предложений, без вопросов."
    )
    messages = [
        {"role": "system", "content": _summarize_prompt},
        *exchanges,
        {"role": "user", "content": closing_instruction},
    ]
    resp = await _client.chat.completions.create(
        model=OPENAI_MODEL,
        messages=messages,
        temperature=0.4,
    )
    return (resp.choices[0].message.content or "").strip()


async def review_query(query: str, catalog: str, history: Optional[list[dict]] = None) -> dict:
    """Ответ на свободный запрос про базу. Returns {'type', 'answer', 'suggested_additions'}."""
    user_msg = "\n\n".join([
        "mode: review",
        f"query: {query}",
        f"catalog:\n{catalog or '(база пуста)'}",
    ])

    messages = [{"role": "system", "content": _system("review")}]
    if history:
        messages.extend(history)
    messages.append({"role": "user", "content": user_msg})

    data = await _chat_json(messages, temperature=0.4)
    data.setdefault("answer", "")
    data.setdefault("suggested_additions", [])
    return data
