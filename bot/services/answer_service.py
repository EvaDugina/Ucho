"""Применение анализа LLM к графу: запись raw/профиля, дедуп, создание черновиков.

Это предметная логика «capture-first»: LLM присылает только ``observations``
(анализ), а ИДЕНТИЧНОСТЬ концептов, slug, create-vs-update и запись — целиком на
коде здесь. Модуль НЕ зависит от aiogram/session — на вход идёт уже готовый
``result``-dict (из ``llm.process_answer``), на выходе ``(created, updated)``.
Это и делает его юнит-тестируемым без Telegram/AITunnel.

Инварианты (см. CLAUDE.md):
* raw пишется ДОСЛОВНО (реальные Q/A + домен сессии), не из LLM-полей;
* slug выводит код из имени (``slugify``), LLM slug не присылает;
* create-vs-update решает дедуп (имя/алиас → файл → Jaccard);
* новые узлы создаются как ``status="draft"`` — связи/конфликты строит reconcista;
* вся запись обёрнута в ``vault.git_wrap`` (атомарность + откат на исключении).
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from .. import about, graph, moc, qmap, vault
from ..config import DOMAINS
from ..graph import Concept, Evidence
from ..validation import slugify

log = logging.getLogger(__name__)


def apply_processed(
    result: dict,
    q_num: int,
    asked_at: datetime,
    original_question: str,
    original_answer: str,
    session_domain: Optional[str] = None,
) -> tuple[int, int]:
    """Записать в raw и применить анализ LLM к графу. Возвращает (created, updated).

    Транзакция через ``git_wrap``: при исключении внутри — откат поддерева
    пользователя к точке до операции.
    """
    with vault.git_wrap(f"apply_processed Q{q_num}"):
        return _apply_inner(
            result, q_num, asked_at, original_question, original_answer, session_domain
        )


def _verbatim_quote(quote: Optional[str], answer: str) -> str:
    """Цитата для evidence — дословный фрагмент ответа. Если LLM перефразировал
    (нет как подстрока при схлопнутых пробелах) — фолбэк на отрывок самого
    ответа. Гарантия: evidence — реальные слова человека, не выдумка модели."""
    a = answer.strip()
    q = (quote or "").strip()
    if q and " ".join(q.split()).lower() in " ".join(a.split()).lower():
        return q
    return a[:300]


def _apply_inner(
    result: dict,
    q_num: int,
    asked_at: datetime,
    original_question: str,
    original_answer: str,
    session_domain: Optional[str] = None,
) -> tuple[int, int]:
    observations = result.get("observations") or []

    # Портрет пользователя: дёшево применить live-дельту (речь/тон/триггеры).
    # Внутри своя обработка ошибок — граф не пострадает, даже если портрет упадёт.
    about.apply_delta(result.get("user_delta") or {})

    # Домен raw-блока — детерминированно от кода: домен сессии (в нём задан
    # вопрос). Для свободной заметки (/ucho, session_domain=None) — из первого
    # валидного наблюдения, иначе everyday. LLM raw-доменом больше не управляет.
    raw_domain = session_domain if session_domain in DOMAINS else None
    if raw_domain is None:
        raw_domain = next((o.get("domain") for o in observations if o.get("domain") in DOMAINS), None)
    if raw_domain is None:
        raw_domain = "everyday"

    # raw — дословно Q + A (источник правды); профиль — дословный ответ человека.
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
        fragment=original_answer,
        raw_time=asked_at.strftime("%H:%M"),
    )

    # Obsidian-native: ссылка на конкретный Q-блок (^Q<n>) — клик в концепте
    # ведёт в точное место raw.
    raw_ref = f"[[00_raw/qna/{asked_at.strftime('%Y-%m-%d')}#^Q{q_num}]]"
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    session_marker = f"chat · {now_str}"
    touched_domains: set[str] = set()
    created = updated = 0

    # Каждое наблюдение LLM → код решает: дубль (дописать evidence) или новый
    # черновик. slug код выводит сам из имени; LLM slug/create-vs-update не шлёт.
    for obs in observations:
        try:
            name = (obs.get("name") or "").strip()
            if not name:
                continue
            domain = obs.get("domain") if obs.get("domain") in DOMAINS else raw_domain
            summary = obs.get("summary") or ""
            quote = _verbatim_quote(obs.get("quote"), original_answer)
            slug = slugify(name)

            # Дедуп (всё в коде): по имени/алиасу → по существующему slug-файлу →
            # по Jaccard на summary. Совпало — обновляем, иначе новый draft.
            existing = graph.resolve_slug(name, domain=domain)
            if not existing and slug:
                existing = graph.resolve_slug(slug, domain=domain)
            if not existing and summary:
                similar = graph.find_similar_concept(summary, domain)
                if similar is not None:
                    existing = similar.slug
                    vault.append_log(
                        "info", "dedup_jaccard",
                        f"obs {name!r} overlaps {similar.slug!r} ≥0.7 → update",
                    )

            if existing:
                graph.append_evidence(existing, Evidence(when=now_str, text=quote, raw_ref=raw_ref))
                if name.lower() != existing.lower():
                    graph.add_alias(existing, name)
                updated += 1
                touched_domains.add(domain)
                continue

            if not slug:
                vault.append_log("warn", "bad_obs_name", f"name={name!r} → пустой slug, пропуск")
                continue
            # capture-first: создаём ЧЕРНОВИК (status=draft). Связи/конфликты
            # строит reconcista, не бот.
            concept = Concept(
                slug=slug,
                name=name,
                type=obs.get("type", "claim"),
                domain=domain,
                summary=summary,
                status="draft",
                evidence=[Evidence(when=now_str, text=quote, raw_ref=raw_ref)],
                source_session=session_marker,
            )
            if graph.save_concept(concept) is not None:
                created += 1
                touched_domains.add(domain)
        except Exception:
            log.exception("failed to apply observation %r", obs)

    # Пересобрать MOC для затронутых доменов (атомарно, внутри git_wrap).
    for d in touched_domains:
        try:
            moc.rebuild_domain_moc(d)
        except Exception:
            log.exception("failed to rebuild MOC for domain %s", d)

    # Вопрос отвечен — пометим в карте. Повторный reply/answer на него теперь
    # пойдёт как Q-повтор (новый q_num). No-op для q_num не из карты (/ucho).
    try:
        qmap.mark_answered(q_num)
    except Exception:
        log.exception("failed to mark q_num=%s answered in qmap", q_num)

    return created, updated
