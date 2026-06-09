"""Применение анализа LLM к графу мировоззрения: raw, дедуп, draft-атомы.

Это предметная логика «capture-first»: LLM присылает только ``observations``
(анализ), а ИДЕНТИЧНОСТЬ концептов, slug, create-vs-update и запись — целиком на
коде здесь. Модуль НЕ зависит от aiogram/session — на вход идёт уже готовый
``result``-dict (из ``llm.process_answer``), на выходе ``(created, updated)``.
Это и делает его юнит-тестируемым без Telegram/live-LLM provider.

Инварианты (см. CLAUDE.md):
* raw пишется ДОСЛОВНО (реальные Q/A + target вопроса), не из LLM-полей;
* slug выводит код из имени (``slugify``), LLM slug не присылает;
* create-vs-update решает дедуп (имя/алиас → файл → Jaccard);
* новые узлы создаются как ``status="draft"`` — связи/конфликты строит reconcista;
* вся запись обёрнута в ``vault.git_wrap`` (атомарность + откат на исключении).
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from .. import about, qmap, vault, worldview
from ..validation import slugify
from ..worldview import Evidence, WorldviewAtom
from ..worldview_taxonomy import coerce_target, legacy_domain_target

log = logging.getLogger(__name__)


def apply_processed(
    result: dict,
    q_num: int,
    asked_at: datetime,
    original_question: str,
    original_answer: str,
    target: Optional[dict] = None,
    session_domain: Optional[str] = None,
) -> tuple[int, int]:
    """Записать в raw и применить анализ LLM к графу. Возвращает (created, updated).

    Транзакция через ``git_wrap``: при исключении внутри — откат поддерева
    пользователя к точке до операции.
    """
    with vault.git_wrap(f"apply_processed Q{q_num}"):
        return _apply_inner(
            result, q_num, asked_at, original_question, original_answer, target, session_domain
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
    target: Optional[dict] = None,
    session_domain: Optional[str] = None,
) -> tuple[int, int]:
    observations = result.get("worldview_observations") or result.get("observations") or []

    # Портрет пользователя: дёшево применить live-дельту (речь/тон/триггеры).
    # Внутри своя обработка ошибок — граф не пострадает, даже если портрет упадёт.
    about.apply_delta(result.get("user_delta") or {})

    # Target raw-блока — детерминированно от кода: target вопроса. Для
    # свободной заметки берём первый валидный атом; для legacy domain — мэппинг.
    raw_target = None
    if isinstance(target, str) and session_domain is None:
        session_domain = target
        target = None
    if isinstance(target, dict):
        raw_target = coerce_target(target.get("area"), target.get("category"), target.get("theme"))
    if raw_target is None and session_domain:
        raw_target = legacy_domain_target(session_domain)
    if raw_target is None:
        first = next((o for o in observations if isinstance(o, dict)), None)
        if first:
            raw_target = coerce_target(first.get("area"), first.get("category"), first.get("theme"))
    if raw_target is None:
        raw_target = coerce_target(None, None, None)

    # raw — дословно Q + A (источник правды).
    vault.append_raw(
        q_num=q_num,
        when=asked_at,
        question=original_question,
        answer=original_answer,
        area=raw_target["area"],
        category=raw_target["category"],
        theme=raw_target["theme"],
        theme_key=raw_target["theme_key"],
    )

    # Obsidian-native: ссылка на конкретный Q-блок (^Q<n>) — клик в концепте
    # ведёт в точное место raw.
    raw_ref = f"[[00_raw/qna/{asked_at.strftime('%Y-%m-%d')}#^Q{q_num}]]"
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    session_marker = f"chat · {now_str}"
    touched_areas: set[str] = set()
    created = updated = 0

    # Каждое наблюдение LLM → код решает: дубль (дописать evidence) или новый
    # черновик. slug код выводит сам из имени; LLM slug/create-vs-update не шлёт.
    for obs in observations:
        try:
            name = (obs.get("name") or "").strip()
            if not name:
                continue
            obs_target = coerce_target(
                obs.get("area") or raw_target["area"],
                obs.get("category") or raw_target["category"],
                obs.get("theme") or raw_target["theme"],
            )
            summary = obs.get("summary") or ""
            quote = _verbatim_quote(obs.get("quote"), original_answer)
            slug = slugify(name)

            # Дедуп (всё в коде): по имени/алиасу → по существующему slug-файлу →
            # по Jaccard на summary. Поиск по всему worldview-графу.
            existing = worldview.resolve_slug(name)
            if not existing and slug:
                existing = worldview.resolve_slug(slug)
            if not existing and summary:
                similar = worldview.find_similar_atom(summary)
                if similar is not None:
                    existing = similar.slug
                    vault.append_log(
                        "info", "dedup_jaccard",
                        f"obs {name!r} overlaps {similar.slug!r} ≥0.7 → update",
                    )

            if existing:
                worldview.append_evidence(existing, Evidence(when=now_str, text=quote, raw_ref=raw_ref))
                if name.lower() != existing.lower():
                    worldview.add_alias(existing, name)
                existing_atom = worldview.load_atom(existing)
                updated += 1
                if existing_atom is not None:
                    touched_areas.add(existing_atom.area)
                continue

            if not slug:
                vault.append_log("warn", "bad_obs_name", f"name={name!r} → пустой slug, пропуск")
                continue
            # capture-first: создаём ЧЕРНОВИК (status=draft). Связи/конфликты
            # строит reconcista, не бот.
            atom = WorldviewAtom(
                slug=slug,
                name=name,
                type=obs.get("type", "claim"),
                area=obs_target["area"],
                category=obs_target["category"],
                theme=obs_target["theme"],
                summary=summary,
                status="draft",
                evidence=[Evidence(when=now_str, text=quote, raw_ref=raw_ref)],
                related=_relation_slugs(obs.get("related")),
                influences=_relation_slugs(obs.get("influences")),
                confidence=obs.get("confidence"),
                source_session=session_marker,
            )
            if worldview.save_atom(atom) is not None:
                created += 1
                touched_areas.add(obs_target["area"])
        except Exception:
            log.exception("failed to apply observation %r", obs)

    # Пересобрать MOC для затронутых областей (атомарно, внутри git_wrap).
    for area in touched_areas:
        try:
            worldview.rebuild_area_moc(area)
        except Exception:
            log.exception("failed to rebuild worldview MOC for area %s", area)

    # Вопрос отвечен — пометим в карте. Повторный reply/answer на него теперь
    # пойдёт как Q-повтор (новый q_num). No-op для q_num не из карты (/ucho).
    try:
        qmap.mark_answered(q_num)
    except Exception:
        log.exception("failed to mark q_num=%s answered in qmap", q_num)

    return created, updated


def _relation_slugs(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        slug = str(item or "").strip()
        if not slug:
            continue
        out.append(slug)
    return out
