"""Механический self-check базы при старте контейнера (без LLM).

Запускается из main.py после ensure_layout. Делает дёшево и детерминированно:
- пересобирает все per-domain MOC,
- валидирует frontmatter-связи концептов (битые [[slug]]),
- ищет дубли (Jaccard) и сирот (ноль связей),
- пишет отчёт в .psycho/startup-check.md.

Глубокий смысловой реиндекс (слияние дублей, переписать profile, кластеризация
MOC по смыслу) — НЕ здесь. Это ручной weekly-review из сильного агента (Claude):
локальная Qwen 14B для такого слишком слаба, а контейнер кроме неё ничего не
поднимает. Здесь — только механика.
"""
from __future__ import annotations

import logging
from datetime import datetime

from . import graph, moc, vault
from .atomic import atomic_write_text
from .config import DOMAINS, PSYCHO_META_DIR

log = logging.getLogger(__name__)

STARTUP_CHECK_PATH = PSYCHO_META_DIR / "startup-check.md"


def run() -> dict:
    """Прогнать self-check. Возвращает summary-dict (для лога/тестов)."""
    all_concepts: list[graph.Concept] = []
    for d in DOMAINS:
        all_concepts.extend(graph.find_concepts(domain=d, limit=10_000))
    known = graph.all_slugs_set()

    # 1. MOC rebuild по всем доменам.
    moc_rebuilt = 0
    for d in DOMAINS:
        try:
            moc.rebuild_domain_moc(d)
            moc_rebuilt += 1
        except Exception:
            log.exception("selfcheck: MOC rebuild failed for %s", d)

    # 2. Битые связи во frontmatter (target не существует ни как slug, ни как alias).
    broken: list[tuple[str, str, str]] = []
    for c in all_concepts:
        for kind in graph.RELATION_KINDS:
            for target in c.relations(kind):
                if target in known:
                    continue
                if graph.resolve_slug(target) is None:
                    broken.append((c.slug, kind, target))

    # 3. Сироты — ноль связей любого вида.
    orphans = [
        c.slug for c in all_concepts
        if not any(c.relations(k) for k in graph.RELATION_KINDS)
    ]

    # 4. Дубли — Jaccard внутри домена (исключая сам концепт).
    dupes: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for c in all_concepts:
        if not c.summary:
            continue
        similar = graph.find_similar_concept(c.summary, c.domain)
        if similar is not None and similar.slug != c.slug:
            pair = tuple(sorted((c.slug, similar.slug)))
            if pair not in seen:
                seen.add(pair)
                dupes.append(pair)

    _write_report(len(all_concepts), moc_rebuilt, broken, orphans, dupes)

    summary = {
        "concepts": len(all_concepts),
        "moc_rebuilt": moc_rebuilt,
        "broken_links": len(broken),
        "orphans": len(orphans),
        "dupes": len(dupes),
    }
    vault.append_log("info", "startup_selfcheck", str(summary))
    return summary


def _write_report(
    total: int,
    moc_rebuilt: int,
    broken: list[tuple[str, str, str]],
    orphans: list[str],
    dupes: list[tuple[str, str]],
) -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [
        "---",
        "type: startup-check",
        f"generated: {ts}",
        "---",
        "",
        f"# Self-check · {ts}",
        "",
        "_Механическая проверка при старте контейнера. Глубокий реиндекс — `weekly-review` из Claude._",
        "",
        f"- Концептов: **{total}**",
        f"- MOC пересобрано: **{moc_rebuilt}/{len(DOMAINS)}**",
        f"- Битых связей: **{len(broken)}**",
        f"- Сирот (без связей): **{len(orphans)}**",
        f"- Кандидатов в дубли: **{len(dupes)}**",
        "",
    ]
    if broken:
        lines += ["## Битые связи", ""]
        lines += [f"- `{s}` → `{kind}` → `{t}` (цель не найдена)" for s, kind, t in broken]
        lines.append("")
    if orphans:
        lines += ["## Сироты (ноль связей)", ""]
        lines += [f"- [[{s}]]" for s in orphans]
        lines.append("")
    if dupes:
        lines += ["## Кандидаты в дубли (Jaccard ≥ 0.7)", ""]
        lines += [f"- [[{a}]] ≈ [[{b}]]" for a, b in dupes]
        lines.append("")
    if not (broken or orphans or dupes):
        lines += ["Проблем не найдено.", ""]

    PSYCHO_META_DIR.mkdir(parents=True, exist_ok=True)
    atomic_write_text(STARTUP_CHECK_PATH, "\n".join(lines).rstrip() + "\n")
