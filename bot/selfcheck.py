"""Механический self-check базы при старте контейнера (без LLM), multi-user.

Проверяет новый граф мировоззрения 01-04:
- пересобирает area-MOC;
- валидирует межпапочные связи атомов;
- ловит self-link и несимметричные `contradicts`;
- считает сироты и грубые дубли summary.
"""
from __future__ import annotations

import logging
from datetime import datetime

from . import userctx, vault, worldview
from .atomic import atomic_write_text
from .config import PSYCHO_META_DIR, VAULT_PATH
from .worldview_taxonomy import WORLDVIEW_AREAS

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
    atoms = worldview.find_atoms(limit=100_000)
    moc_rebuilt = 0
    for area in WORLDVIEW_AREAS:
        try:
            worldview.rebuild_area_moc(area.key)
            moc_rebuilt += 1
        except Exception:
            log.exception("selfcheck: worldview MOC rebuild failed for uid=%s area=%s", uid, area.key)

    check = worldview.check_links()
    dupes: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for atom in atoms:
        if not atom.summary:
            continue
        similar = worldview.find_similar_atom(atom.summary)
        if similar is not None and similar.slug != atom.slug:
            pair = tuple(sorted((atom.slug, similar.slug)))
            if pair not in seen:
                seen.add(pair)
                dupes.append(pair)

    summary = {
        "atoms": len(atoms),
        "moc_rebuilt": moc_rebuilt,
        "broken_links": len(check["broken"]),
        "self_links": len(check["self_links"]),
        "asymmetric": len(check["asymmetric"]),
        "orphans": len(check["orphans"]),
        "dupes": len(dupes),
    }

    lines = [
        f"## Пользователь {uid}",
        "",
        f"- Атомов: **{len(atoms)}** · MOC: **{moc_rebuilt}/{len(WORLDVIEW_AREAS)}** · "
        f"битых: **{len(check['broken'])}** · self-link: **{len(check['self_links'])}** · "
        f"asym: **{len(check['asymmetric'])}** · сирот: **{len(check['orphans'])}** · "
        f"дублей: **{len(dupes)}**",
        "",
    ]
    lines += [f"  - битая связь `{s}` → `{kind}` → `{t}`" for s, kind, t in check["broken"]]
    lines += [f"  - self-link `{s}` → `{kind}`" for s, kind in check["self_links"]]
    lines += [f"  - asym `{s}` → `{kind}` → `{t}`" for s, kind, t in check["asymmetric"]]
    lines += [f"  - сирота `{s}`" for s in check["orphans"]]
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
        "_Механическая проверка мировоззренческого графа 01-04 при старте (без LLM)._",
        "",
        f"Пользователей: **{len(per_user)}**.",
        "",
    ]
    body = sections or ["_Пользователей с данными нет._", ""]
    PSYCHO_META_DIR.mkdir(parents=True, exist_ok=True)
    atomic_write_text(STARTUP_CHECK_PATH, "\n".join(head + body).rstrip() + "\n")
