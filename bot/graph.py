"""Граф концептов в Obsidian: одна нота на концепт, связи во frontmatter и в теле.

Файлы лежат в <vault>/concepts/<domain>/<slug>.md. Сохранение — всегда полная
перезапись через save_concept(), чтобы не разъезжаться по форматам.
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
from .config import DOMAINS
from .validation import (
    safe_evidence_text,
    safe_name,
    safe_open_question,
    safe_slug,
    safe_summary,
)
from .vault import append_log

log = logging.getLogger(__name__)

def concepts_dir() -> Path:
    """Папка концептов ТЕКУЩЕГО пользователя: `<vault>/users/<uid>/concepts/`."""
    return userctx.user_root() / "concepts"


RELATION_KINDS = ("supports", "contradicts", "derived_from", "related")
CONCEPT_TYPES = ("principle", "value", "preference", "belief", "claim")
# draft  — создан ботом live, без связей/конфликтов, ждёт промоушна Claude.
# stable — выверен Claude в скилле reconcista.
# tentative / contested — промежуточные пометки Claude.
CONCEPT_STATUSES = ("draft", "stable", "tentative", "contested")

# Если save_concept обнаруживает, что target изменён извне (Obsidian / YandexDisk
# pull / ручная правка), он по умолчанию НЕ перезаписывает — ручная правка
# приоритетнее. Чтобы это переопределить (например, в скриптах миграции),
# передавай force=True.
DRIFT_LOG_OP = "drift_skipped"


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
    aliases: list[str] = field(default_factory=list)  # альтернативные формулировки (Obsidian aliases)
    evidence: list[Evidence] = field(default_factory=list)
    open_questions: list[str] = field(default_factory=list)
    contradiction_notes: list[dict] = field(default_factory=list)  # [{with_slug, note}]
    source_session: str = ""  # chat:<id> или просто заметка о происхождении
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

# Вики-ссылка вида [[slug]] или [[path/foo|alias]]. Применяется только к телу
# концепта — ссылки в frontmatter валидируются отдельно (там это просто
# элементы списков supports/contradicts/...).
_WIKILINK_RE = re.compile(r"\[\[([^\[\]\n|#]+)(\|[^\[\]\n]+)?\]\]")


def _ensure_layout() -> None:
    concepts_dir().mkdir(parents=True, exist_ok=True)
    for d in DOMAINS:
        (concepts_dir() / d).mkdir(exist_ok=True)


def _path_for(slug: str, domain: str) -> Path:
    return concepts_dir() / domain / f"{slug}.md"


def _is_meta_file(p: Path) -> bool:
    """Служебный файл в папке домена (не концепт): начинается с `_` (старый
    `_moc.md`) ИЛИ это MOC-нода с именем = домен заглавными (`AESTHETICS.md`).
    MOC теперь называется по домену, чтобы на графе узел подписывался категорией,
    а не «_moc» — поэтому такие файлы надо исключать из перечисления концептов.
    """
    if p.name.startswith("_"):
        return True
    return p.stem == p.parent.name.upper()


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


def save_concept(c: Concept, force: bool = False) -> Optional[Path]:
    """Записать концепт. Возвращает Path при успехе, None если пропустили
    из-за drift или невалидного slug.

    * **Slug**: пропускается через ``safe_slug`` — kebab-case ASCII, max 80,
      без path-separator'ов. Невалидный → отказ (None) с warn в log.md.
      Это критичный barrier против path traversal (``slug="../../etc"``)
      и кривых имён файлов на Windows (``slug="con"``, etc.).
    * **type/status**: при невалидном значении coerce + warn (не raise,
      чтобы не валить весь _apply_processed из-за одного поля).
    * **domain**: raise ValueError — домен критичен для маршрутизации.
    * **name/summary/evidence/open_questions**: проходят через ``safe_*``
      санитизацию (длины, переводы строк, YAML-разделители).
    * **Drift**: перед записью сверяем mtime/size с manifest; при внешней
      правке (Obsidian / YandexDisk-pull) и ``force=False`` пропускаем.
    * **Запись**: атомарная (tmp + os.replace), после успеха — manifest.record.
    """
    _ensure_layout()

    if c.domain not in DOMAINS:
        raise ValueError(f"unknown domain: {c.domain}")

    # Slug — критичная проверка: имя файла на диске.
    original_slug = c.slug
    c.slug = safe_slug(c.slug)
    if not c.slug:
        append_log(
            "error",
            "bad_concept_slug",
            f"slug={original_slug!r} — invalid (path traversal / empty / non-ASCII), skipped",
        )
        log.warning("invalid slug %r, skipping concept", original_slug)
        return None
    if c.slug != original_slug:
        append_log(
            "warn",
            "slug_sanitized",
            f"{original_slug!r} → {c.slug!r}",
        )

    if c.type not in CONCEPT_TYPES:
        append_log("warn", "bad_concept_type", f"slug={c.slug} type={c.type!r} → coerced to 'claim'")
        log.warning("unknown concept type %r, coercing to 'claim'", c.type)
        c.type = "claim"
    if c.status not in CONCEPT_STATUSES:
        append_log("warn", "bad_concept_status", f"slug={c.slug} status={c.status!r} → 'tentative'")
        c.status = "tentative"

    # Текстовые поля — санитизация на длину/переводы строк/спец-разделители.
    c.name = safe_name(c.name) or c.slug
    c.summary = safe_summary(c.summary)
    c.evidence = [
        Evidence(
            when=safe_name(e.when),
            text=safe_evidence_text(e.text),
            raw_ref=e.raw_ref,  # raw_ref формируется нами, не LLM
        )
        for e in c.evidence
    ]
    c.open_questions = [safe_open_question(q) for q in c.open_questions if q]
    c.open_questions = [q for q in c.open_questions if q]

    # relation buckets: санитизируем slug-и связей; невалидные молча выкидываем
    # (логируется на уровне add_relation/_render).
    for kind in ("supports", "contradicts", "derived_from", "related"):
        bucket: list[str] = []
        seen: dict[str, None] = {}
        for raw_s in getattr(c, kind):
            s = safe_slug(raw_s)
            if not s or s == c.slug:
                continue
            if s not in seen:
                seen[s] = None
                bucket.append(s)
        setattr(c, kind, bucket)

    if not c.created:
        c.created = _today()
    c.updated = _today()

    p = _path_for(c.slug, c.domain)

    if not force and p.exists() and manifest.check_drift(p):
        append_log(
            "warn",
            DRIFT_LOG_OP,
            f"slug={c.slug} domain={c.domain} — external edit detected, refusing to overwrite",
        )
        log.warning("drift detected on %s, refusing to overwrite", p)
        return None

    atomic_write_text(p, _render(c))
    manifest.record(p)
    return p


def append_evidence(slug: str, evidence: Evidence, domain: Optional[str] = None) -> Optional[Path]:
    slug_safe = safe_slug(slug)
    if not slug_safe:
        return None
    c = load_concept(slug_safe, domain)
    if c is None:
        return None
    # save_concept сам санитизирует evidence-текст; здесь только защита от
    # очевидного мусора (пустая цитата).
    if evidence and evidence.text:
        c.evidence.append(evidence)
    return save_concept(c)


def append_open_question(slug: str, question: str, domain: Optional[str] = None) -> Optional[Path]:
    slug_safe = safe_slug(slug)
    if not slug_safe:
        return None
    c = load_concept(slug_safe, domain)
    if c is None:
        return None
    q_clean = safe_open_question(question)
    if q_clean and q_clean not in c.open_questions:
        c.open_questions.append(q_clean)
    return save_concept(c)


def patch_summary(slug: str, new_summary: str, domain: Optional[str] = None) -> Optional[Path]:
    slug_safe = safe_slug(slug)
    if not slug_safe:
        return None
    c = load_concept(slug_safe, domain)
    if c is None:
        return None
    c.summary = safe_summary(new_summary)
    return save_concept(c)


def add_alias(slug: str, alias: str, domain: Optional[str] = None) -> Optional[Path]:
    """Добавить alias к существующему концепту (если ещё нет такого)."""
    slug_safe = safe_slug(slug)
    if not slug_safe:
        return None
    alias_clean = safe_name(alias)
    if not alias_clean:
        return None
    c = load_concept(slug_safe, domain)
    if c is None:
        return None
    # не добавляем alias если он совпадает с именем, slug-ом или уже в списке
    lowered = {a.lower() for a in c.aliases}
    if alias_clean.lower() in lowered:
        return None
    if alias_clean.lower() == c.name.lower() or alias_clean.lower() == c.slug.lower():
        return None
    c.aliases.append(alias_clean)
    return save_concept(c)


def add_contradiction_note(slug: str, other_slug: str, note: str, domain: Optional[str] = None) -> Optional[Path]:
    """Добавить `[!contradiction]` callout к концепту slug со ссылкой на other_slug."""
    slug_safe = safe_slug(slug)
    other_safe = safe_slug(other_slug)
    if not slug_safe or not other_safe or slug_safe == other_safe:
        return None
    c = load_concept(slug_safe, domain)
    if c is None:
        return None
    note_clean = safe_open_question(note)
    # дедуп: не добавляем если уже есть такая пара (slug, note)
    for cn in c.contradiction_notes:
        if cn.get("with_slug") == other_safe and cn.get("note") == note_clean:
            return None
    c.contradiction_notes.append({"with_slug": other_safe, "note": note_clean})
    return save_concept(c)


def add_relation(from_slug: str, to_slug: str, kind: str, note: Optional[str] = None) -> None:
    """Добавляет связь from→to. Для симметричных видов добавляет и обратную.

    Slug-и нормализуются через ``safe_slug`` — защита от path traversal и
    пустых/невалидных значений от LLM. Note также санитизируется (без \\n).
    """
    if kind not in RELATION_KINDS:
        append_log("warn", "bad_relation_kind", f"kind={kind!r} from={from_slug!r} to={to_slug!r}")
        log.warning("unknown relation kind %r, dropped", kind)
        return

    from_safe = safe_slug(from_slug)
    to_safe = safe_slug(to_slug)
    if not from_safe or not to_safe:
        append_log(
            "warn",
            "bad_relation_slug",
            f"from={from_slug!r} to={to_slug!r} — invalid slug(s), dropped",
        )
        return
    if from_safe == to_safe:
        return  # self-loop игнорируем тихо

    a = load_concept(from_safe)
    b = load_concept(to_safe)
    if a is None or b is None:
        log.warning("add_relation: missing concept(s): %s / %s", from_safe, to_safe)
        return

    a.add_relation(kind, b.slug)
    if kind in ("supports", "contradicts", "related"):
        b.add_relation(kind, a.slug)
    elif kind == "derived_from":
        b.add_relation("related", a.slug)  # обратная ссылка как "related"

    if note:
        note_clean = safe_open_question(note)
        marker = f"- связь с [[{b.slug}]] ({kind}): {note_clean}"
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
        for p in (concepts_dir() / d).glob("*.md"):
            if _is_meta_file(p):  # _moc / MOC-нода категории — не концепт
                continue
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
        for p in (concepts_dir() / d).glob("*.md"):
            if _is_meta_file(p):  # _moc / MOC-нода категории — не концепт
                continue
            c = _parse_file(p)
            if c:
                out.append({"slug": c.slug, "name": c.name, "domain": c.domain, "summary": c.summary})
    return out


def all_slugs_set() -> set[str]:
    """Множество всех существующих slug в любом домене — для wikilink-валидации."""
    return {x["slug"] for x in all_slugs()}


def _filter_wikilinks(text: str, valid_slugs: set[str], owner_slug: str) -> str:
    """Заменить невалидные [[X]] в теле концепта на plain X и залогировать.

    * Извлекаем target — если есть alias `|`, берём часть до `|`.
    * Поддерживаются ссылки на `raw/...`, `profile/...`, `concepts/...` —
      они не slug'и графа, пропускаем без проверки.
    * Само-ссылку (на owner_slug) тоже пропускаем без warn — это редко, но
      допустимо.
    """
    def repl(m: re.Match[str]) -> str:
        target = m.group(1).strip()
        # внешние якоря — точно не slug графа
        head = target.split("/", 1)[0]
        if head in {"raw", "profile", "concepts", ""}:
            return m.group(0)
        if target == owner_slug or target in valid_slugs:
            return m.group(0)
        append_log(
            "warn",
            "broken_wikilink",
            f"in concept {owner_slug!r}: [[{target}]] → no such slug, downgraded to plain text",
        )
        # alias-фолбэк: если был [[X|alias]], отображаем alias; иначе X.
        alias = (m.group(2) or "")[1:].strip()
        return alias or target

    return _WIKILINK_RE.sub(repl, text)


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
    """Рендер концепта в Obsidian-native формате (callouts + aliases + block refs).

    Структура:
        ---
        <frontmatter с aliases>
        ---

        # <name>

        > [!summary] TL;DR
        > <summary>

        > [!quote]
        > <text>
        > — [[raw/<date>#^Q<n>]] · <when>
        ... (по одному callout на evidence)

        > [!contradiction] vs [[<other-slug>]]
        > <note>

        > [!question]
        > <open question>

        > [!source]
        > <session>

    Старый формат (## Подтверждения / ## Открытые вопросы) парсер по-прежнему
    читает — это нужно для миграции. Новых файлов в старом формате мы не пишем.
    """
    # Снаружи валидируем slug-ссылки во frontmatter и теле, чтобы файл не уезжал
    # с broken wikilinks. Самого себя считаем валидным; raw/profile/concepts/...
    # это не slug графа — пропускаем без проверки.
    valid = all_slugs_set()
    valid.add(c.slug)

    def keep(slug: str) -> bool:
        if not slug:
            return False
        if slug == c.slug or slug in valid:
            return True
        append_log(
            "warn",
            "broken_wikilink",
            f"in concept {c.slug!r} frontmatter: [[{slug}]] not in vault, dropped",
        )
        return False

    fm: dict[str, object] = {
        "type": c.type,
        "domain": c.domain,
        "slug": c.slug,
        "created": c.created,
        "updated": c.updated,
        "status": c.status,
        "supports": [_wikilink(s) for s in c.supports if keep(s)],
        "contradicts": [_wikilink(s) for s in c.contradicts if keep(s)],
        "derived_from": [_wikilink(s) for s in c.derived_from if keep(s)],
        "related": [_wikilink(s) for s in c.related if keep(s)],
    }
    if c.aliases:
        # Obsidian-native: aliases в frontmatter позволяют резолвить [[alt-name]]
        # в этот концепт; используется ``resolve_slug``.
        fm["aliases"] = list(c.aliases)

    yaml_text = yaml.safe_dump(fm, allow_unicode=True, sort_keys=False, default_flow_style=False)
    parts: list[str] = [f"---\n{yaml_text}---", "", f"# {c.name}", ""]

    if c.summary:
        parts.append("> [!summary] TL;DR")
        for line in c.summary.splitlines():
            parts.append(f"> {line}")
        parts.append("")

    for e in c.evidence:
        parts.append("> [!quote]")
        for line in (e.text or "").splitlines():
            parts.append(f"> {line}")
        # Атрибуция: ссылка на конкретный Q-блок в raw + when.
        attribution_bits = []
        if e.raw_ref:
            attribution_bits.append(e.raw_ref)
        if e.when:
            attribution_bits.append(e.when)
        if attribution_bits:
            parts.append(f"> — {' · '.join(attribution_bits)}")
        parts.append("")

    for cn in c.contradiction_notes:
        other = cn.get("with_slug", "")
        note = cn.get("note", "")
        if not other:
            continue
        if not keep(other):
            continue
        parts.append(f"> [!contradiction] vs [[{other}]]")
        for line in (note or "").splitlines():
            parts.append(f"> {line}")
        parts.append("")

    for q in c.open_questions:
        parts.append("> [!question]")
        for line in (q or "").splitlines():
            parts.append(f"> {line}")
        parts.append("")

    if c.source_session:
        parts.append("> [!source]")
        parts.append(f"> {c.source_session}")
        parts.append("")

    body = "\n".join(parts).rstrip() + "\n"
    # И на финальном теле прогоняем filter для inline-ссылок.
    return _filter_wikilinks(body, valid, c.slug)


def _parse_file(path: Path) -> Optional[Concept]:
    """Парсер концепта. Поддерживает оба формата:

    * Старый: ``## Подтверждения``, ``## Открытые вопросы``, summary как
      обычный абзац под H1.
    * Новый: callouts ``> [!summary]``, ``> [!quote]``, ``> [!question]``,
      ``> [!contradiction]``, ``> [!source]``.

    Если в теле есть хоть один callout — приоритет за новым форматом.
    Это нужно для миграции: старые файлы продолжают читаться, новые пишутся
    в callout-формате.
    """
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
    is_callout_format = "> [!" in body

    if is_callout_format:
        summary = _extract_callout(body, "summary")
        evidence = _extract_evidence_callouts(body)
        open_questions = _extract_callouts_all(body, "question")
        contradiction_notes = _extract_contradiction_callouts(body)
        source_session = _extract_callout(body, "source")
    else:
        summary = _extract_summary(body)
        evidence = _extract_evidence(body)
        open_questions = _extract_open_questions(body)
        contradiction_notes = []
        source_session = ""

    aliases_raw = fm.get("aliases") or []
    aliases: list[str] = []
    for a in aliases_raw:
        s = str(a).strip()
        if s:
            aliases.append(s)

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
        aliases=aliases,
        evidence=evidence,
        open_questions=open_questions,
        contradiction_notes=contradiction_notes,
        source_session=source_session,
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


# ---------- callout parsers (новый формат) ----------

# Заголовок callout: `> [!<type>] [optional inline title]`
_CALLOUT_HEAD_RE = re.compile(r"^>\s*\[!(?P<type>[a-z]+)\][+-]?\s*(?P<title>.*)$")
# Атрибуция в `> [!quote]` callout: `> — [[raw/...|...]] · YYYY-MM-DD HH:MM`
_QUOTE_ATTR_RE = re.compile(
    r"^>\s*—\s*(?P<ref>\[\[[^\]]+\]\])(?:\s*·\s*(?P<when>.+))?\s*$"
)
# Заголовок [!contradiction]: `> [!contradiction] vs [[<slug>]]`
_CONTRA_HEAD_RE = re.compile(
    r"^>\s*\[!contradiction\][+-]?\s*(?:vs\s+)?\[\[(?P<slug>[^\]]+)\]\]\s*$"
)


def _iter_callouts(body: str):
    """Yield (type, title, lines[]) для каждого callout в теле."""
    current_type: Optional[str] = None
    current_title: str = ""
    current_lines: list[str] = []
    for raw in body.splitlines():
        m = _CALLOUT_HEAD_RE.match(raw)
        if m and not raw.lstrip().startswith(">>"):
            # новый callout начинается — закроем предыдущий
            if current_type is not None:
                yield current_type, current_title, current_lines
            current_type = m.group("type").lower()
            current_title = m.group("title").strip()
            current_lines = []
            continue
        if current_type is not None:
            if raw.startswith(">"):
                current_lines.append(raw[1:].lstrip(" "))
            else:
                # пустая или не-> строка → callout закрылся
                yield current_type, current_title, current_lines
                current_type, current_title, current_lines = None, "", []
    if current_type is not None:
        yield current_type, current_title, current_lines


def _extract_callout(body: str, want_type: str) -> str:
    """Первый callout указанного типа → text (без атрибуции). Пустая строка если нет."""
    for ctype, _title, lines in _iter_callouts(body):
        if ctype != want_type:
            continue
        # отрезаем хвостовую атрибуцию (для [!quote])
        text_lines = [ln for ln in lines if not _QUOTE_ATTR_RE.match("> " + ln)]
        return "\n".join(text_lines).strip()
    return ""


def _extract_callouts_all(body: str, want_type: str) -> list[str]:
    out: list[str] = []
    for ctype, _title, lines in _iter_callouts(body):
        if ctype != want_type:
            continue
        text = "\n".join(lines).strip()
        if text:
            out.append(text)
    return out


def _extract_evidence_callouts(body: str) -> list[Evidence]:
    out: list[Evidence] = []
    for ctype, _title, lines in _iter_callouts(body):
        if ctype != "quote":
            continue
        # последняя строка — атрибуция (если матчится)
        text_lines = list(lines)
        when = ""
        raw_ref = ""
        if text_lines:
            attr_match = _QUOTE_ATTR_RE.match("> " + text_lines[-1])
            if attr_match:
                raw_ref = attr_match.group("ref").strip()
                when = (attr_match.group("when") or "").strip()
                text_lines = text_lines[:-1]
        text = "\n".join(text_lines).strip()
        if text:
            out.append(Evidence(when=when, text=text, raw_ref=raw_ref))
    return out


def _extract_contradiction_callouts(body: str) -> list[dict]:
    out: list[dict] = []
    # Нужно проитерировать с доступом к raw-заголовку — _iter_callouts даёт title.
    # Регекс title мы матчим отдельно.
    for ctype, title, lines in _iter_callouts(body):
        if ctype != "contradiction":
            continue
        # Пытаемся вытащить slug из title: "vs [[slug]]" или просто "[[slug]]"
        slug = ""
        m_title = re.match(r"(?:vs\s+)?\[\[([^\]]+)\]\]", title.strip())
        if m_title:
            slug = _strip_wikilink(m_title.group(1))
        if not slug:
            continue
        note = "\n".join(lines).strip()
        out.append({"with_slug": slug, "note": note})
    return out


# ---------- dedup (Jaccard на n-граммах) ----------


_TOKEN_RE = re.compile(r"[a-zа-яё0-9]+", re.IGNORECASE)


def _tokens(text: str) -> list[str]:
    return [t.lower() for t in _TOKEN_RE.findall(text or "")]


def _bigrams(text: str) -> set[tuple[str, str]]:
    toks = _tokens(text)
    return {(a, b) for a, b in zip(toks, toks[1:])}


def _jaccard(a: set, b: set) -> float:
    if not a and not b:
        return 0.0
    return len(a & b) / max(1, len(a | b))


def find_similar_concept(summary: str, domain: str, threshold: float = 0.7) -> Optional[Concept]:
    """Найти концепт того же домена с высоким Jaccard-overlap по summary.

    Сравниваются множества биграмм нормализованных токенов. Порог 0.7 значит:
    «70% биграмм нового summary встречаются у существующего». Подобранное на
    глаз значение — пересмотрим если будет много ложных срабатываний.

    Возвращает первое попадание; если нужен топ-N — придётся переписать.
    Используется как сигнал «переадресуй в update вместо create».
    """
    if domain not in DOMAINS:
        return None
    target = _bigrams(summary)
    if len(target) < 3:
        # слишком короткий summary — Jaccard ненадёжен, не работаем.
        return None
    for p in (concepts_dir() / domain).glob("*.md"):
        if _is_meta_file(p):
            continue
        c = _parse_file(p)
        if c is None or not c.summary:
            continue
        sim = _jaccard(target, _bigrams(c.summary))
        if sim >= threshold:
            return c
    return None


# ---------- alias resolution ----------


def resolve_slug(candidate: str, domain: Optional[str] = None) -> Optional[str]:
    """Найти каноничный slug для строки, которую вернула LLM.

    Алгоритм:
    1. Прогнать через ``safe_slug`` — нормализация.
    2. Если такой slug существует как файл → вернуть его.
    3. Иначе проходим по всем (или в данном домене) концептам и ищем
       соответствие в их ``aliases`` (case-insensitive по сравнению ASCII).
    4. Не нашли → None.

    Используется в handlers перед `concepts_to_create`, чтобы LLM-альтернатива
    вроде «честность-слова» резолвилась в существующий «chestnost».
    """
    norm = safe_slug(candidate)
    if not norm:
        # Может быть alias в человеческой форме, не slug — пробуем матчить как есть.
        norm = ""

    # 1. прямое совпадение по нормализованному slug
    if norm:
        if domain:
            if _path_for(norm, domain).exists():
                return norm
        else:
            for d in DOMAINS:
                if _path_for(norm, d).exists():
                    return norm

    # 2. поиск по aliases во всех концептах нужного домена
    candidate_norm = candidate.strip().lower()
    domains = [domain] if domain else list(DOMAINS)
    for d in domains:
        for p in (concepts_dir() / d).glob("*.md"):
            if _is_meta_file(p):
                continue
            c = _parse_file(p)
            if not c:
                continue
            for alias in c.aliases:
                if alias.strip().lower() == candidate_norm:
                    return c.slug
                if safe_slug(alias) and safe_slug(alias) == norm:
                    return c.slug
            # name тоже считается «алиасом» (если LLM прислала русскую формулировку)
            if c.name.strip().lower() == candidate_norm:
                return c.slug
    return None
