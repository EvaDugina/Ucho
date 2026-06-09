"""Граф мировоззрения в Obsidian: атомы 01-04 и связи между уровнями.

Новый runtime-контур пишет черновики в:
`<area_folder>/atoms/<slug>.md`, где `area_folder` — одна из папок 01-04.
Связи хранятся path-wikilink'ами, чтобы Obsidian видел связи и внутри уровня,
и между уровнями: `[[02_Миропонимание/atoms/slug|Название]]`.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Iterable, Optional

import yaml

from . import manifest, userctx
from .atomic import atomic_write_text
from .validation import (
    safe_evidence_text,
    safe_name,
    safe_open_question,
    safe_slug,
    safe_summary,
)
from .vault import append_log
from .worldview_taxonomy import (
    WORLDVIEW_TYPES,
    coerce_target,
    get_area,
    get_category,
)

log = logging.getLogger(__name__)

RELATION_KINDS = ("supports", "contradicts", "derived_from", "related", "influences", "manifests_as")
SYMMETRIC_RELATIONS = {"contradicts"}
ATOM_STATUSES = ("draft", "stable", "tentative", "contested")
DRIFT_LOG_OP = "worldview_drift_skipped"


@dataclass
class Evidence:
    when: str
    text: str
    raw_ref: str


@dataclass
class WorldviewAtom:
    slug: str
    name: str
    area: str
    category: str
    theme: str
    type: str
    summary: str = ""
    status: str = "draft"
    aliases: list[str] = field(default_factory=list)
    evidence: list[Evidence] = field(default_factory=list)
    supports: list[str] = field(default_factory=list)
    contradicts: list[str] = field(default_factory=list)
    derived_from: list[str] = field(default_factory=list)
    related: list[str] = field(default_factory=list)
    influences: list[str] = field(default_factory=list)
    manifests_as: list[str] = field(default_factory=list)
    confidence: float | None = None
    source_session: str = ""
    created: str = ""
    updated: str = ""

    def relations(self, kind: str) -> list[str]:
        return getattr(self, kind)

    def add_relation(self, kind: str, target: str) -> None:
        if kind not in RELATION_KINDS:
            raise ValueError(f"unknown relation kind: {kind}")
        bucket = self.relations(kind)
        if target == self.slug or target in bucket:
            return
        bucket.append(target)


_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n(.*)$", re.DOTALL)
_WIKILINK_RE = re.compile(r"\[\[([^\[\]\n|#]+)(?:\|[^\[\]\n]+)?\]\]")
_CALLOUT_HEAD_RE = re.compile(r"^>\s*\[!(?P<type>[a-z_]+)\][+-]?\s*(?P<title>.*)$")
_QUOTE_ATTR_RE = re.compile(r"^>\s*—\s*(?P<ref>\[\[[^\]]+\]\])(?:\s*·\s*(?P<when>.+))?\s*$")
_TOKEN_RE = re.compile(r"[a-zа-яё0-9]+", re.IGNORECASE)


def worldview_root() -> Path:
    return userctx.user_root()


def area_dir(area: str) -> Path:
    a = get_area(area)
    if a is None:
        raise ValueError(f"unknown worldview area: {area}")
    return worldview_root() / a.folder


def atoms_dir(area: str) -> Path:
    return area_dir(area) / "atoms"


def _ensure_layout() -> None:
    from .worldview_taxonomy import WORLDVIEW_AREAS

    for area in WORLDVIEW_AREAS:
        atoms_dir(area.key).mkdir(parents=True, exist_ok=True)


def _path_for(slug: str, area: str) -> Path:
    return atoms_dir(area) / f"{slug}.md"


def _today() -> str:
    return date.today().isoformat()


def _strip_wikilink(s: object) -> str:
    text = str(s or "").strip()
    if text.startswith("[[") and text.endswith("]]"):
        text = text[2:-2]
    if "|" in text:
        text = text.split("|", 1)[0]
    if "/" in text:
        text = text.rsplit("/", 1)[-1]
    return text.strip()


def _display_link(slug: str, label: str | None = None) -> str:
    atom = load_atom(slug)
    if atom is None:
        return f"[[{slug}]]"
    area = get_area(atom.area)
    path = f"{area.folder}/atoms/{atom.slug}" if area else atom.slug
    title = label or atom.name or atom.slug
    return f"[[{path}|{title}]]"


def _relation_targets(values: Iterable[object]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in values or []:
        slug = _strip_wikilink(raw)
        slug = safe_slug(slug) or slug
        if not slug or slug in seen:
            continue
        seen.add(slug)
        out.append(slug)
    return out


def load_atom(slug: str, area: Optional[str] = None) -> Optional[WorldviewAtom]:
    _ensure_layout()
    safe = safe_slug(slug) or str(slug or "").strip()
    if not safe:
        return None
    if area:
        p = _path_for(safe, area)
        return _parse_file(p) if p.exists() else None
    for p in sorted(worldview_root().glob("0[1-4]_*" + "/atoms/*.md")):
        if p.stem == safe:
            return _parse_file(p)
    return None


def save_atom(atom: WorldviewAtom, force: bool = False) -> Optional[Path]:
    _ensure_layout()
    target = coerce_target(atom.area, atom.category, atom.theme)
    atom.area = target["area"]
    atom.category = target["category"]
    atom.theme = target["theme"]

    original_slug = atom.slug
    atom.slug = safe_slug(atom.slug)
    if not atom.slug:
        append_log("error", "bad_worldview_slug", f"slug={original_slug!r} invalid, skipped")
        return None
    if atom.type not in WORLDVIEW_TYPES:
        append_log("warn", "bad_worldview_type", f"slug={atom.slug} type={atom.type!r} -> claim")
        atom.type = "claim"
    if atom.status not in ATOM_STATUSES:
        append_log("warn", "bad_worldview_status", f"slug={atom.slug} status={atom.status!r} -> draft")
        atom.status = "draft"

    atom.name = safe_name(atom.name) or atom.slug
    atom.summary = safe_summary(atom.summary)
    atom.aliases = [safe_name(a) for a in atom.aliases if safe_name(a)]
    atom.evidence = [
        Evidence(when=safe_name(e.when), text=safe_evidence_text(e.text), raw_ref=e.raw_ref)
        for e in atom.evidence
        if e and e.text
    ]

    for kind in RELATION_KINDS:
        cleaned: list[str] = []
        seen: set[str] = set()
        for raw in getattr(atom, kind):
            slug = _strip_wikilink(raw)
            slug = safe_slug(slug) or slug
            if not slug or slug == atom.slug or slug in seen:
                continue
            seen.add(slug)
            cleaned.append(slug)
        setattr(atom, kind, cleaned)

    if not atom.created:
        atom.created = _today()
    atom.updated = _today()

    path = _path_for(atom.slug, atom.area)
    if not force and path.exists() and manifest.check_drift(path):
        append_log("warn", DRIFT_LOG_OP, f"slug={atom.slug} area={atom.area} external edit detected")
        log.warning("drift detected on %s, refusing to overwrite", path)
        return None

    atomic_write_text(path, _render(atom))
    manifest.record(path)
    return path


def append_evidence(slug: str, evidence: Evidence, area: Optional[str] = None) -> Optional[Path]:
    atom = load_atom(slug, area=area)
    if atom is None:
        return None
    if evidence and evidence.text:
        atom.evidence.append(evidence)
    return save_atom(atom)


def add_alias(slug: str, alias: str, area: Optional[str] = None) -> Optional[Path]:
    atom = load_atom(slug, area=area)
    alias_clean = safe_name(alias)
    if atom is None or not alias_clean:
        return None
    lowered = {a.lower() for a in atom.aliases}
    if alias_clean.lower() in lowered or alias_clean.lower() in {atom.name.lower(), atom.slug.lower()}:
        return None
    atom.aliases.append(alias_clean)
    return save_atom(atom)


def add_relation(from_slug: str, to_slug: str, kind: str) -> None:
    if kind not in RELATION_KINDS:
        append_log("warn", "bad_worldview_relation_kind", f"kind={kind!r}")
        return
    a = load_atom(from_slug)
    b = load_atom(to_slug)
    if a is None or b is None or a.slug == b.slug:
        return
    a.add_relation(kind, b.slug)
    save_atom(a)
    if kind in SYMMETRIC_RELATIONS:
        b.add_relation(kind, a.slug)
        save_atom(b)


def find_atoms(
    *,
    area: Optional[str] = None,
    category: Optional[str] = None,
    slugs: Optional[Iterable[str]] = None,
    limit: int = 30,
) -> list[WorldviewAtom]:
    _ensure_layout()
    out: list[WorldviewAtom] = []
    wanted_slugs = set(slugs) if slugs else None
    dirs: list[Path]
    if area and get_area(area):
        dirs = [atoms_dir(area)]
    else:
        dirs = [p for p in sorted(worldview_root().glob("0[1-4]_*" + "/atoms")) if p.is_dir()]
    for directory in dirs:
        for p in sorted(directory.glob("*.md")):
            if wanted_slugs is not None and p.stem not in wanted_slugs:
                continue
            atom = _parse_file(p)
            if atom is None:
                continue
            if category and atom.category != category:
                continue
            out.append(atom)
            if len(out) >= limit:
                return out
    return out


def all_slugs() -> list[dict]:
    out: list[dict] = []
    for atom in find_atoms(limit=100_000):
        out.append({
            "slug": atom.slug,
            "name": atom.name,
            "area": atom.area,
            "category": atom.category,
            "theme": atom.theme,
            "summary": atom.summary,
        })
    return out


def all_slugs_set() -> set[str]:
    return {x["slug"] for x in all_slugs()}


def resolve_slug(candidate: str, *, area: Optional[str] = None) -> Optional[str]:
    norm = safe_slug(candidate)
    if norm:
        atom = load_atom(norm, area=area)
        if atom is not None:
            return atom.slug
    candidate_norm = (candidate or "").strip().lower()
    for atom in find_atoms(area=area, limit=100_000):
        if atom.name.strip().lower() == candidate_norm:
            return atom.slug
        for alias in atom.aliases:
            if alias.strip().lower() == candidate_norm:
                return atom.slug
            if norm and safe_slug(alias) == norm:
                return atom.slug
    return None


def context_snapshot(atoms: list[WorldviewAtom], max_chars: int = 6000) -> str:
    lines: list[str] = []
    for atom in atoms:
        rel_bits = []
        for kind in RELATION_KINDS:
            values = atom.relations(kind)
            if values:
                rel_bits.append(f"{kind}: {', '.join(values)}")
        rel = " | ".join(rel_bits) if rel_bits else "—"
        lines.append(
            f"- [{atom.area}/{atom.category}/{atom.theme}] {atom.slug} "
            f"({atom.name}, {atom.type}): {atom.summary} ({rel})"
        )
        if sum(len(x) for x in lines) > max_chars:
            lines.append("- ... (truncated)")
            break
    return "\n".join(lines) if lines else "(база мировоззрения пуста)"


def rebuild_area_moc(area: str) -> Path:
    a = get_area(area)
    if a is None:
        raise ValueError(f"unknown worldview area: {area}")
    directory = area_dir(a.key)
    directory.mkdir(parents=True, exist_ok=True)
    atoms = find_atoms(area=a.key, limit=100_000)
    by_category: dict[str, list[WorldviewAtom]] = {c.key: [] for c in a.categories}
    for atom in atoms:
        by_category.setdefault(atom.category, []).append(atom)
    lines = [
        "---",
        "type: worldview-moc",
        f"area: {a.key}",
        f"area_folder: {a.folder}",
        "---",
        "",
        f"# {a.folder}",
        "",
        a.description,
        "",
        "_Автоматическая карта области. Атомы лежат в `atoms/`._",
        "",
    ]
    if not atoms:
        lines += ["_Пока ни одного атома._", ""]
    for category in a.categories:
        items = by_category.get(category.key) or []
        lines.append(f"## {category.title} (`{category.key}`) ({len(items)})")
        lines.append("")
        lines.append(category.description)
        lines.append("")
        if not items:
            lines.append("_Пока пусто._")
            lines.append("")
            continue
        for atom in sorted(items, key=lambda x: x.name.lower()):
            summary = (atom.summary or "").strip().split("\n", 1)[0]
            if len(summary) > 200:
                summary = summary[:200].rstrip() + "..."
            summary_part = f" — {summary}" if summary else ""
            lines.append(f"- [[atoms/{atom.slug}|{atom.name}]] `theme: {atom.theme}`{summary_part}")
        lines.append("")
    path = directory / "MOC.md"
    atomic_write_text(path, "\n".join(lines).rstrip() + "\n")
    return path


def check_links() -> dict:
    """Механическая проверка атомов: битые связи, self-link, symmetry, orphan."""
    atoms = find_atoms(limit=100_000)
    known = {a.slug: a for a in atoms}
    broken: list[tuple[str, str, str]] = []
    self_links: list[tuple[str, str]] = []
    asym: list[tuple[str, str, str]] = []
    orphan: list[str] = []
    for atom in atoms:
        has_rel = False
        for kind in RELATION_KINDS:
            values = atom.relations(kind)
            has_rel = has_rel or bool(values)
            for target in values:
                if target == atom.slug:
                    self_links.append((atom.slug, kind))
                    continue
                other = known.get(target)
                if other is None:
                    broken.append((atom.slug, kind, target))
                    continue
                if kind in SYMMETRIC_RELATIONS and atom.slug not in other.relations(kind):
                    asym.append((atom.slug, kind, target))
        if not has_rel:
            orphan.append(atom.slug)
    return {
        "atoms": len(atoms),
        "broken": broken,
        "self_links": self_links,
        "asymmetric": asym,
        "orphans": orphan,
    }


def _render(atom: WorldviewAtom) -> str:
    def rel(kind: str) -> list[str]:
        return [_display_link(s) for s in atom.relations(kind)]

    fm: dict[str, object] = {
        "area": atom.area,
        "category": atom.category,
        "theme": atom.theme,
        "type": atom.type,
        "slug": atom.slug,
        "created": atom.created,
        "updated": atom.updated,
        "status": atom.status,
        "aliases": atom.aliases,
        "evidence": [
            {"when": e.when, "text": e.text, "raw_ref": e.raw_ref}
            for e in atom.evidence
        ],
        "supports": rel("supports"),
        "contradicts": rel("contradicts"),
        "derived_from": rel("derived_from"),
        "related": rel("related"),
        "influences": rel("influences"),
        "manifests_as": rel("manifests_as"),
    }
    if atom.confidence is not None:
        fm["confidence"] = atom.confidence
    if atom.source_session:
        fm["source_session"] = atom.source_session
    yaml_text = yaml.safe_dump(fm, allow_unicode=True, sort_keys=False, default_flow_style=False)
    parts = [f"---\n{yaml_text}---", "", f"# {atom.name}", ""]
    if atom.summary:
        parts.append("> [!summary] TL;DR")
        for line in atom.summary.splitlines():
            parts.append(f"> {line}")
        parts.append("")
    for e in atom.evidence:
        parts.append("> [!quote]")
        for line in (e.text or "").splitlines():
            parts.append(f"> {line}")
        bits = [x for x in (e.raw_ref, e.when) if x]
        if bits:
            parts.append(f"> — {' · '.join(bits)}")
        parts.append("")
    if atom.source_session:
        parts.append("> [!source]")
        parts.append(f"> {atom.source_session}")
        parts.append("")
    return "\n".join(parts).rstrip() + "\n"


def _parse_file(path: Path) -> Optional[WorldviewAtom]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return None
    try:
        fm = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError:
        log.exception("failed to parse worldview frontmatter in %s", path)
        return None
    body = m.group(2)
    target = coerce_target(fm.get("area"), fm.get("category"), fm.get("theme"))
    evidence_raw = fm.get("evidence") or []
    evidence: list[Evidence] = []
    if isinstance(evidence_raw, list):
        for item in evidence_raw:
            if isinstance(item, dict):
                evidence.append(Evidence(
                    when=str(item.get("when") or ""),
                    text=str(item.get("text") or ""),
                    raw_ref=str(item.get("raw_ref") or ""),
                ))
    if not evidence:
        evidence = _extract_evidence_callouts(body)
    aliases = [str(a).strip() for a in (fm.get("aliases") or []) if str(a).strip()]
    name = _extract_title(body) or str(fm.get("name") or path.stem)
    return WorldviewAtom(
        slug=str(fm.get("slug") or path.stem),
        name=name,
        area=target["area"],
        category=target["category"],
        theme=target["theme"],
        type=str(fm.get("type") or "claim"),
        summary=_extract_callout(body, "summary") or str(fm.get("summary") or ""),
        status=str(fm.get("status") or "draft"),
        aliases=aliases,
        evidence=evidence,
        supports=_relation_targets(fm.get("supports") or []),
        contradicts=_relation_targets(fm.get("contradicts") or []),
        derived_from=_relation_targets(fm.get("derived_from") or []),
        related=_relation_targets(fm.get("related") or []),
        influences=_relation_targets(fm.get("influences") or []),
        manifests_as=_relation_targets(fm.get("manifests_as") or []),
        confidence=_coerce_confidence(fm.get("confidence")),
        source_session=str(fm.get("source_session") or _extract_callout(body, "source") or ""),
        created=str(fm.get("created") or ""),
        updated=str(fm.get("updated") or ""),
    )


def _extract_title(body: str) -> str:
    for line in body.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return ""


def _iter_callouts(body: str):
    current_type: Optional[str] = None
    current_lines: list[str] = []
    for raw in body.splitlines():
        m = _CALLOUT_HEAD_RE.match(raw)
        if m and not raw.lstrip().startswith(">>"):
            if current_type is not None:
                yield current_type, current_lines
            current_type = m.group("type").lower()
            current_lines = []
            continue
        if current_type is not None:
            if raw.startswith(">"):
                current_lines.append(raw[1:].lstrip(" "))
            else:
                yield current_type, current_lines
                current_type, current_lines = None, []
    if current_type is not None:
        yield current_type, current_lines


def _extract_callout(body: str, want_type: str) -> str:
    for ctype, lines in _iter_callouts(body):
        if ctype != want_type:
            continue
        text_lines = [ln for ln in lines if not _QUOTE_ATTR_RE.match("> " + ln)]
        return "\n".join(text_lines).strip()
    return ""


def _extract_evidence_callouts(body: str) -> list[Evidence]:
    out: list[Evidence] = []
    for ctype, lines in _iter_callouts(body):
        if ctype != "quote":
            continue
        text_lines = list(lines)
        when = ""
        raw_ref = ""
        if text_lines:
            m = _QUOTE_ATTR_RE.match("> " + text_lines[-1])
            if m:
                raw_ref = m.group("ref").strip()
                when = (m.group("when") or "").strip()
                text_lines = text_lines[:-1]
        text = "\n".join(text_lines).strip()
        if text:
            out.append(Evidence(when=when, text=text, raw_ref=raw_ref))
    return out


def _coerce_confidence(value: object) -> float | None:
    if value is None or value == "":
        return None
    try:
        return round(max(0.0, min(1.0, float(value))), 2)
    except (TypeError, ValueError):
        return None


def _tokens(text: str) -> list[str]:
    return [t.lower() for t in _TOKEN_RE.findall(text or "")]


def _bigrams(text: str) -> set[tuple[str, str]]:
    toks = _tokens(text)
    return {(a, b) for a, b in zip(toks, toks[1:])}


def _jaccard(a: set, b: set) -> float:
    if not a and not b:
        return 0.0
    return len(a & b) / max(1, len(a | b))


def find_similar_atom(summary: str, *, area: Optional[str] = None, threshold: float = 0.7) -> Optional[WorldviewAtom]:
    target = _bigrams(summary)
    if len(target) < 3:
        return None
    for atom in find_atoms(area=area, limit=100_000):
        if not atom.summary:
            continue
        if _jaccard(target, _bigrams(atom.summary)) >= threshold:
            return atom
    return None
