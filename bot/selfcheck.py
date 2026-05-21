"""Механический self-check базы при старте контейнера (без LLM), multi-user.

Запускается из main.py после layout. Для КАЖДОГО пользователя (`users/<uid>/`)
дёшево и детерминированно:
- пересобирает все per-domain MOC,
- валидирует frontmatter-связи концептов (битые [[slug]]),
- ищет дубли (Jaccard) и сирот (ноль связей).
Сводный отчёт по всем пользователям — в глобальный `.psycho/startup-check.md`.

Глубокий смысловой реиндекс (слияние, profile, кластеризация MOC) — НЕ здесь.
Это ручной weekly-review из сильного агента (Claude).
"""
from __future__ import annotations

import logging
from datetime import datetime

from . import graph, moc, userctx, vault
from .atomic import atomic_write_text
from .config import DOMAINS, PSYCHO_META_DIR, VAULT_PATH

log = logging.getLogger(__name__)

STARTUP_CHECK_PATH = PSYCHO_META_DIR / "startup-check.md"


def _user_ids() -> list[int]:
    users_dir = VAULT_PATH / "users"
    if not users_dir.exists():
        return []
    return sorted(
        int(p.name) for p in users_dir.iterdir() if p.is_dir() and p.name.isdigit()
    )


def run() -> dict:
    """Self-check по всем пользователям. Возвращает {uid: summary}."""
    uids = _user_ids()
    per_user: dict[int, dict] = {}
    report_sections: list[str] = []

    for uid in uids:
        userctx.set_user(uid)
        summary, section = _run_one(uid)
        per_user[uid] = summary
        report_sections.append(section)

    _write_report(per_user, report_sections)
    vault.append_log("info", "startup_selfcheck", str({u: s for u, s in per_user.items()}))
    return per_user


def _run_one(uid: int) -> tuple[dict, str]:
    """Проверка одного пользователя (контекст уже выставлен). (summary, md-секция)."""
    all_concepts: list[graph.Concept] = []
    for d in DOMAINS:
        all_concepts.extend(graph.find_concepts(domain=d, limit=10_000))
    known = graph.all_slugs_set()

    moc_rebuilt = 0
    for d in DOMAINS:
        try:
            moc.rebuild_domain_moc(d)
            moc_rebuilt += 1
        except Exception:
            log.exception("selfcheck: MOC rebuild failed for uid=%s domain=%s", uid, d)

    broken: list[tuple[str, str, str]] = []
    for c in all_concepts:
        for kind in graph.RELATION_KINDS:
            for target in c.relations(kind):
                if target in known:
                    continue
                if graph.resolve_slug(target) is None:
                    broken.append((c.slug, kind, target))

    orphans = [
        c.slug for c in all_concepts
        if not any(c.relations(k) for k in graph.RELATION_KINDS)
    ]

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

    summary = {
        "concepts": len(all_concepts),
        "moc_rebuilt": moc_rebuilt,
        "broken_links": len(broken),
        "orphans": len(orphans),
        "dupes": len(dupes),
    }

    lines = [
        f"## Пользователь {uid}",
        "",
        f"- Концептов: **{len(all_concepts)}** · MOC: **{moc_rebuilt}/{len(DOMAINS)}** · "
        f"битых: **{len(broken)}** · сирот: **{len(orphans)}** · дублей: **{len(dupes)}**",
        "",
    ]
    if broken:
        lines += [f"  - битая связь `{s}` → `{kind}` → `{t}`" for s, kind, t in broken]
    if orphans:
        lines += [f"  - сирота `{s}`" for s in orphans]
    if dupes:
        lines += [f"  - дубль `{a}` ≈ `{b}`" for a, b in dupes]
    lines.append("")
    return summary, "\n".join(lines)


def _write_report(per_user: dict[int, dict], sections: list[str]) -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    head = [
        "---",
        "type: startup-check",
        f"generated: {ts}",
        "---",
        "",
        f"# Self-check · {ts}",
        "",
        "_Механическая проверка при старте (без LLM), по всем пользователям._",
        "",
        f"Пользователей: **{len(per_user)}**.",
        "",
    ]
    body = sections or ["_Пользователей с данными нет._", ""]
    PSYCHO_META_DIR.mkdir(parents=True, exist_ok=True)
    atomic_write_text(STARTUP_CHECK_PATH, "\n".join(head + body).rstrip() + "\n")
