"""Map of Content (MOC) per-domain — Obsidian-native навигация по концептам.

Один MOC-файл на домен: ``concepts/<domain>/<DOMAIN>.md`` (имя = домен
заглавными, чтобы узел графа подписывался названием темы, а не «_moc»). Содержит список
всех концептов домена, сгруппированных по ``type`` (principle / value /
preference / belief / claim), с одно-строчным summary под каждым.

Пересобирается каждый раз после изменения концептов внутри домена —
вызывается в ``handlers._apply_processed_inner`` внутри той же
``git_wrap`` транзакции. Запись атомарная.

На графе Obsidian MOC-нода — хаб темы (узел `AESTHETICS`, `ETHICS`, …) со
связями ко всем концептам домена. Из перечисления концептов MOC-файл
исключается (см. graph._is_meta_file: `_*` или stem == домен.upper()).
"""
from __future__ import annotations

import logging
from pathlib import Path

from .atomic import atomic_write_text
from .config import DOMAINS
from .graph import CONCEPT_TYPES, _parse_file, concepts_dir

log = logging.getLogger(__name__)

# Человеко-читаемые заголовки. Сохраняем порядок CONCEPT_TYPES, чтобы MOC
# всегда выглядел одинаково.
_TYPE_LABELS = {
    "principle": "Принципы",
    "value": "Ценности",
    "preference": "Предпочтения",
    "belief": "Убеждения",
    "claim": "Утверждения",
}


def _moc_path(domain: str) -> Path:
    # Имя файла = домен ЗАГЛАВНЫМИ (`AESTHETICS.md`), чтобы на графе Obsidian
    # узел-хаб подписывался названием категории, а не «_moc».
    return concepts_dir() / domain / f"{domain.upper()}.md"


def _legacy_moc_path(domain: str) -> Path:
    return concepts_dir() / domain / "_moc.md"


def rebuild_domain_moc(domain: str) -> Path:
    """Перезаписать MOC-ноду домена (`<DOMAIN>.md`). Возвращает путь.

    Старый `_moc.md` (если остался от прежней схемы) удаляется.
    """
    if domain not in DOMAINS:
        raise ValueError(f"unknown domain: {domain}")
    domain_dir = concepts_dir() / domain
    domain_dir.mkdir(parents=True, exist_ok=True)

    # Удаляем легаси `_moc.md`, чтобы не висел вторым узлом на графе.
    legacy = _legacy_moc_path(domain)
    if legacy.exists():
        try:
            legacy.unlink()
        except OSError:
            pass

    moc_name = _moc_path(domain).stem  # сам MOC-файл — не концепт, отсеиваем
    # Собираем концепты домена (исключая служебные: _* и саму MOC-ноду).
    concepts: dict[str, list[tuple[str, str, str]]] = {t: [] for t in CONCEPT_TYPES}
    others: list[tuple[str, str, str, str]] = []  # неизвестный type
    for p in sorted(domain_dir.glob("*.md")):
        if p.name.startswith("_") or p.stem == moc_name:
            continue
        c = _parse_file(p)
        if c is None:
            continue
        summary = (c.summary or "").strip().split("\n", 1)[0]
        if len(summary) > 200:
            summary = summary[:200].rstrip() + "…"
        entry = (c.slug, c.name, summary)
        if c.type in concepts:
            concepts[c.type].append(entry)
        else:
            others.append((c.type, *entry))

    lines: list[str] = [
        "---",
        "type: moc",
        f"domain: {domain}",
        "---",
        "",
        f"# {domain.upper()}",
        "",
        "_Карта темы. Автоматически перестраивается ботом — не редактируй вручную._",
        "",
    ]
    total = sum(len(v) for v in concepts.values()) + len(others)
    if total == 0:
        lines += ["_Пока ни одного концепта._", ""]
    else:
        for t in CONCEPT_TYPES:
            items = concepts.get(t, [])
            if not items:
                continue
            lines.append(f"## {_TYPE_LABELS.get(t, t.capitalize())} ({len(items)})")
            lines.append("")
            for slug, name, summary in items:
                summary_part = f" — {summary}" if summary else ""
                lines.append(f"- [[{slug}|{name}]]{summary_part}")
            lines.append("")
        if others:
            lines.append("## Прочее")
            lines.append("")
            for t, slug, name, summary in others:
                summary_part = f" — {summary}" if summary else ""
                lines.append(f"- ({t}) [[{slug}|{name}]]{summary_part}")
            lines.append("")

    path = _moc_path(domain)
    atomic_write_text(path, "\n".join(lines).rstrip() + "\n")
    return path
