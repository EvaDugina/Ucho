"""Map of Content (MOC) per-domain — Obsidian-native навигация по концептам.

Один MOC-файл на домен: ``concepts/<domain>/_moc.md``. Содержит список
всех концептов домена, сгруппированных по ``type`` (principle / value /
preference / belief / claim), с одно-строчным summary под каждым.

Пересобирается каждый раз после изменения концептов внутри домена —
вызывается в ``handlers._apply_processed_inner`` внутри той же
``git_wrap`` транзакции. Запись атомарная.

Файл начинается с underscore (`_moc.md`) — в Obsidian это обычно скрывает
их из поиска по имени, но они остаются доступны через `[[_moc]]` и Graph
View. Можно также поставить тег в frontmatter для фильтрации.
"""
from __future__ import annotations

import logging
from pathlib import Path

from .atomic import atomic_write_text
from .config import DOMAINS, VAULT_PATH
from .graph import CONCEPTS_DIR, CONCEPT_TYPES, _parse_file

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
    return CONCEPTS_DIR / domain / "_moc.md"


def rebuild_domain_moc(domain: str) -> Path:
    """Перезаписать `_moc.md` для домена. Возвращает путь."""
    if domain not in DOMAINS:
        raise ValueError(f"unknown domain: {domain}")
    domain_dir = CONCEPTS_DIR / domain
    domain_dir.mkdir(parents=True, exist_ok=True)

    # Собираем концепты домена. _moc.md сам тоже окажется в glob, отсеиваем.
    concepts: dict[str, list[tuple[str, str, str]]] = {t: [] for t in CONCEPT_TYPES}
    others: list[tuple[str, str, str, str]] = []  # неизвестный type
    for p in sorted(domain_dir.glob("*.md")):
        if p.name.startswith("_"):
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
        f"# MOC — {domain}",
        "",
        "_Автоматически перестраивается ботом. Не редактируй вручную — правки потеряются._",
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
