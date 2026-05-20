"""Граф концептов в Obsidian: одна нота на концепт, связи во frontmatter и в теле.

Файлы лежат в <vault>/concepts/<domain>/<slug>.md. Сохранение — всегда полная
перезапись через save_concept(), чтобы не разъезжаться по форматам.
"""
from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Iterable, Optional

import yaml

from .config import DOMAINS, VAULT_PATH

log = logging.getLogger(__name__)

CONCEPTS_DIR = VAULT_PATH / "concepts"

RELATION_KINDS = ("supports", "contradicts", "derived_from", "related")
CONCEPT_TYPES = ("principle", "value", "preference", "belief", "claim")
CONCEPT_STATUSES = ("stable", "tentative", "contested")


@dataclass
class Evidence:
    when: str          # "2026-05-18 14:32"
    text: str          # цитата/перефраз из ответа
    raw_ref: str       # "[[raw/2026-05-18#1432]]"


@dataclass
class Concept:
    slug: str
    name: str
    type: str
    domain: str
    summary: str = ""
    status: str = "tentative"
    supports: list[str] = field(default_factory=list)
    contradicts: list[str] = field(default_factory=list)
    derived_from: list[str] = field(default_factory=list)
    related: list[str] = field(default_factory=list)
    evidence: list[Evidence] = field(default_factory=list)
    open_questions: list[str] = field(default_factory=list)
    created: str = ""
    updated: str = ""

    def relations(self, kind: str) -> list[str]:
        return getattr(self, kind)

    def add_relation(self, kind: str, slug: str) -> None:
        if kind not in RELATION_KINDS:
            raise ValueError(f"unknown relation kind: {kind}")
        bucket = self.relations(kind)
        if slug == self.slug or slug in bucket:
            return
        bucket.append(slug)


# ---------- helpers ----------

_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n(.*)$", re.DOTALL)

# Строка подтверждения вида:  `- 2026-05-20 16:52 — «текст цитаты» — из [[raw/2026-05-20|Q1]]`
_EVIDENCE_LINE_RE = re.compile(
    r"^-\s*(?P<when>.*?)\s*—\s*«(?P<text>.*)»\s*—\s*из\s*(?P<ref>\[\[.+?\]\])\s*$"
)


def _ensure_layout() -> None:
    CONCEPTS_DIR.mkdir(parents=True, exist_ok=True)
    for d in DOMAINS:
        (CONCEPTS_DIR / d).mkdir(exist_ok=True)


def _path_for(slug: str, domain: str) -> Path:
    return CONCEPTS_DIR / domain / f"{slug}.md"


def _wikilink(slug: str) -> str:
    return f"[[{slug}]]"


def _strip_wikilink(s: str) -> str:
    s = s.strip()
    if s.startswith("[[") and s.endswith("]]"):
        s = s[2:-2]
    if "|" in s:
        s = s.split("|", 1)[0]
    return s.strip()


def _today() -> str:
    return date.today().isoformat()


# ---------- public API ----------


def load_concept(slug: str, domain: Optional[str] = None) -> Optional[Concept]:
    _ensure_layout()
    if domain:
        p = _path_for(slug, domain)
        return _parse_file(p) if p.exists() else None
    for d in DOMAINS:
        p = _path_for(slug, d)
        if p.exists():
            return _parse_file(p)
    return None


def save_concept(c: Concept) -> Path:
    _ensure_layout()
    if c.domain not in DOMAINS:
        raise ValueError(f"unknown domain: {c.domain}")
    if c.type not in CONCEPT_TYPES:
        log.warning("unknown concept type %r, coercing to 'claim'", c.type)
        c.type = "claim"
    if c.status not in CONCEPT_STATUSES:
        c.status = "tentative"
    if not c.created:
        c.created = _today()
    c.updated = _today()

    p = _path_for(c.slug, c.domain)
    p.write_text(_render(c), encoding="utf-8")
    return p


def append_evidence(slug: str, evidence: Evidence, domain: Optional[str] = None) -> Optional[Path]:
    c = load_concept(slug, domain)
    if c is None:
        return None
    c.evidence.append(evidence)
    return save_concept(c)


def append_open_question(slug: str, question: str, domain: Optional[str] = None) -> Optional[Path]:
    c = load_concept(slug, domain)
    if c is None:
        return None
    if question not in c.open_questions:
        c.open_questions.append(question)
    return save_concept(c)


def patch_summary(slug: str, new_summary: str, domain: Optional[str] = None) -> Optional[Path]:
    c = load_concept(slug, domain)
    if c is None:
        return None
    c.summary = new_summary
    return save_concept(c)


def add_relation(from_slug: str, to_slug: str, kind: str, note: Optional[str] = None) -> None:
    """Добавляет связь from→to. Для симметричных видов добавляет и обратную."""
    if kind not in RELATION_KINDS:
        raise ValueError(f"unknown relation kind: {kind}")

    a = load_concept(from_slug)
    b = load_concept(to_slug)
    if a is None or b is None:
        log.warning("add_relation: missing concept(s): %s / %s", from_slug, to_slug)
        return

    a.add_relation(kind, b.slug)
    if kind in ("supports", "contradicts", "related"):
        b.add_relation(kind, a.slug)
    elif kind == "derived_from":
        b.add_relation("related", a.slug)  # обратная ссылка как "related"

    if note:
        marker = f"- связь с [[{b.slug}]] ({kind}): {note}"
        if marker not in a.open_questions:
            a.open_questions.append(marker)

    save_concept(a)
    save_concept(b)


def find_concepts(domain: Optional[str] = None, slugs: Optional[Iterable[str]] = None, limit: int = 30) -> list[Concept]:
    _ensure_layout()
    out: list[Concept] = []
    target_slugs = set(slugs) if slugs else None
    domains = [domain] if domain else list(DOMAINS)
    for d in domains:
        for p in (CONCEPTS_DIR / d).glob("*.md"):
            slug = p.stem
            if target_slugs is not None and slug not in target_slugs:
                continue
            parsed = _parse_file(p)
            if parsed:
                out.append(parsed)
            if len(out) >= limit:
                return out
    return out


def all_slugs() -> list[dict]:
    """Возвращает плоский каталог: [{slug, name, domain, summary}]."""
    _ensure_layout()
    out: list[dict] = []
    for d in DOMAINS:
        for p in (CONCEPTS_DIR / d).glob("*.md"):
            c = _parse_file(p)
            if c:
                out.append({"slug": c.slug, "name": c.name, "domain": c.domain, "summary": c.summary})
    return out


def context_snapshot(concepts: list[Concept], max_chars: int = 6000) -> str:
    """Превращает список концептов в компактный текст для подачи в LLM."""
    lines: list[str] = []
    for c in concepts:
        rel_bits = []
        for kind in RELATION_KINDS:
            r = c.relations(kind)
            if r:
                rel_bits.append(f"{kind}: {', '.join(r)}")
        rel_str = " | ".join(rel_bits) if rel_bits else "—"
        line = f"- [{c.domain}] {c.slug} ({c.name}, {c.type}): {c.summary}  ({rel_str})"
        lines.append(line)
        if sum(len(x) for x in lines) > max_chars:
            lines.append("- ... (truncated)")
            break
    return "\n".join(lines) if lines else "(база пуста)"


# ---------- parsing / rendering ----------


def _render(c: Concept) -> str:
    fm = {
        "type": c.type,
        "domain": c.domain,
        "slug": c.slug,
        "created": c.created,
        "updated": c.updated,
        "status": c.status,
        "supports": [_wikilink(s) for s in c.supports],
        "contradicts": [_wikilink(s) for s in c.contradicts],
        "derived_from": [_wikilink(s) for s in c.derived_from],
        "related": [_wikilink(s) for s in c.related],
    }
    yaml_text = yaml.safe_dump(fm, allow_unicode=True, sort_keys=False, default_flow_style=False)
    parts = [f"---\n{yaml_text}---", "", f"# {c.name}", ""]
    if c.summary:
        parts += [c.summary, ""]
    if c.evidence:
        parts += ["## Подтверждения", ""]
        for e in c.evidence:
            parts.append(f"- {e.when} — «{e.text}» — из {e.raw_ref}")
        parts.append("")
    if c.open_questions:
        parts += ["## Открытые вопросы", ""]
        for q in c.open_questions:
            parts.append(f"- {q}")
        parts.append("")
    return "\n".join(parts)


def _parse_file(path: Path) -> Optional[Concept]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    m = _FRONTMATTER_RE.match(text)
    if not m:
        log.warning("concept file %s has no frontmatter, skipping", path)
        return None
    try:
        fm = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError:
        log.exception("failed to parse frontmatter in %s", path)
        return None
    body = m.group(2)

    name = _extract_title(body) or fm.get("slug", path.stem)
    summary = _extract_summary(body)
    evidence = _extract_evidence(body)
    open_questions = _extract_open_questions(body)

    return Concept(
        slug=str(fm.get("slug") or path.stem),
        name=name,
        type=str(fm.get("type", "claim")),
        domain=str(fm.get("domain", path.parent.name)),
        summary=summary,
        status=str(fm.get("status", "tentative")),
        supports=[_strip_wikilink(x) for x in (fm.get("supports") or [])],
        contradicts=[_strip_wikilink(x) for x in (fm.get("contradicts") or [])],
        derived_from=[_strip_wikilink(x) for x in (fm.get("derived_from") or [])],
        related=[_strip_wikilink(x) for x in (fm.get("related") or [])],
        evidence=evidence,
        open_questions=open_questions,
        created=str(fm.get("created") or ""),
        updated=str(fm.get("updated") or ""),
    )


def _extract_title(body: str) -> str:
    for line in body.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return ""


def _extract_summary(body: str) -> str:
    out: list[str] = []
    in_summary = False
    for line in body.splitlines():
        if line.startswith("# "):
            in_summary = True
            continue
        if in_summary:
            if line.startswith("## "):
                break
            if line.strip():
                out.append(line.strip())
    return " ".join(out).strip()


def _extract_evidence(body: str) -> list[Evidence]:
    out: list[Evidence] = []
    in_section = False
    for line in body.splitlines():
        if line.strip().startswith("## Подтверждения"):
            in_section = True
            continue
        if in_section:
            if line.startswith("## "):
                break
            stripped = line.strip()
            if not stripped.startswith("- "):
                continue
            m = _EVIDENCE_LINE_RE.match(stripped)
            if m:
                out.append(Evidence(
                    when=m.group("when").strip(),
                    text=m.group("text").strip(),
                    raw_ref=m.group("ref").strip(),
                ))
            # Если строка не парсится в наш формат — молча игнорируем,
            # чтобы не накапливать корруптированный текст при roundtrip.
    return out


def _extract_open_questions(body: str) -> list[str]:
    out: list[str] = []
    in_section = False
    for line in body.splitlines():
        if line.strip().startswith("## Открытые вопросы"):
            in_section = True
            continue
        if in_section:
            if line.startswith("## "):
                break
            if line.strip().startswith("- "):
                out.append(line.strip()[2:])
    return out
