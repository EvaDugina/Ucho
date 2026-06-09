"""Raw Q&A, free notes, profile fragments and history lookup."""
from __future__ import annotations

import logging
import re
from datetime import datetime
from pathlib import Path

from ..config import DOMAINS
from ..errors import ValidationError, VaultError
from ..storage import layout
from ..storage.log import append_log
from ..validation import escape_raw_block, safe_question_text, safe_user_text
from ..worldview_taxonomy import coerce_target, legacy_domain_target

log = logging.getLogger(__name__)

_ENTRY_RE = re.compile(
    r"##\s+Q(\d+)\s*[·\-—]\s*(\d{2}:\d{2})\s*[·\-—]\s*([^\n]+?)\s*\n"
    r"\*\*Q:\*\*\s*(.*?)\n"
    r"\*\*A:\*\*\s*(.*?)(?=\n##\s+Q\d+\s*[·\-—]|\Z)",
    re.DOTALL,
)


def _topic_from_parts(
    *,
    area: str | None = None,
    category: str | None = None,
    theme: str | None = None,
    domain: str | None = None,
) -> dict:
    if area or category or theme:
        return coerce_target(area, category, theme)
    legacy = legacy_domain_target(domain)
    if legacy:
        return legacy
    return coerce_target(None, None, None)


def append_raw(
    q_num: int,
    when: datetime,
    question: str = "",
    answer: str = "",
    *legacy_args: str,
    area: str | None = None,
    category: str | None = None,
    theme: str | None = None,
    theme_key: str | None = None,
    domain: str | None = None,
) -> Path:
    layout.ensure_layout()
    if legacy_args:
        legacy_domain = question
        question = answer
        answer = legacy_args[0]
        domain = domain or legacy_domain
    target = _topic_from_parts(area=area, category=category, theme=theme, domain=domain)
    topic = theme_key or target["theme_key"]
    date_str = when.strftime("%Y-%m-%d")
    time_str = when.strftime("%H:%M")
    path = layout.raw_dir() / f"{date_str}.md"

    q_clean = escape_raw_block(safe_question_text(question))
    a_clean, a_truncated = safe_user_text(answer)
    if a_truncated:
        append_log("warn", "raw_answer_truncated", f"Q{q_num} length>limit")
    a_clean = escape_raw_block(a_clean)
    block = (
        f"## Q{q_num} · {time_str} · {topic}\n"
        f"**Q:** {q_clean}\n"
        f"**A:** {a_clean}\n"
        f"^Q{q_num}\n\n"
    )
    try:
        if not path.exists():
            path.write_text(f"# {date_str}\n\n", encoding="utf-8")
        with path.open("a", encoding="utf-8") as f:
            f.write(block)
    except OSError as exc:
        raise VaultError(f"append_raw failed for Q{q_num}: {exc}") from exc
    return path


def append_note(when: datetime, text: str) -> Path:
    nd = layout.notes_dir()
    nd.mkdir(parents=True, exist_ok=True)
    date_str = when.strftime("%Y-%m-%d")
    time_str = when.strftime("%H:%M")
    path = nd / f"{date_str}.md"
    clean, truncated = safe_user_text(text)
    if truncated:
        append_log("warn", "note_truncated", f"{date_str} {time_str} length>limit")
    clean = escape_raw_block(clean)
    block = f"## {time_str}\n{clean}\n\n"
    try:
        if not path.exists():
            path.write_text(f"# Заметки · {date_str}\n\n", encoding="utf-8")
        with path.open("a", encoding="utf-8") as f:
            f.write(block)
    except OSError as exc:
        raise VaultError(f"append_note failed for {date_str}: {exc}") from exc
    return path


def append_profile(when: datetime, domain: str, fragment: str, raw_time: str) -> Path:
    if domain not in DOMAINS:
        raise ValidationError(f"unknown domain: {domain}")
    layout.ensure_layout()
    date_str = when.strftime("%Y-%m-%d")
    path = layout.profile_dir() / f"{domain}.md"
    fragment_clean = escape_raw_block(safe_question_text(fragment))
    block = (
        f"### {date_str}\n"
        f"- {fragment_clean} _(из [[00_raw/qna/{date_str}|{raw_time}]])_\n\n"
    )
    try:
        with path.open("a", encoding="utf-8") as f:
            f.write(block)
    except OSError as exc:
        raise VaultError(f"append_profile failed for {domain}: {exc}") from exc
    return path


def iter_history() -> list[dict]:
    rd = layout.raw_dir()
    if not rd.exists():
        return []
    entries: list[dict] = []
    for path in sorted(rd.glob("*.md")):
        date_str = path.stem
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            log.exception("failed to read %s", path)
            continue
        for m in _ENTRY_RE.finditer(text):
            topic = m.group(3).strip()
            area = category = theme = theme_key = ""
            domain = ""
            parts = topic.split("/", 2)
            if len(parts) == 3:
                area, category, theme = parts
                theme_key = topic
            else:
                domain = topic
                legacy = legacy_domain_target(domain)
                if legacy:
                    area = legacy["area"]
                    category = legacy["category"]
                    theme = legacy["theme"]
                    theme_key = legacy["theme_key"]
            entries.append({
                "n": int(m.group(1)),
                "date": date_str,
                "time": m.group(2),
                "area": area,
                "category": category,
                "theme": theme,
                "theme_key": theme_key,
                "domain": domain,
                "question": m.group(4).strip(),
                "answer": m.group(5).strip(),
            })
    entries.sort(key=lambda e: e["n"])
    return entries


def find_question(n: int) -> dict | None:
    for e in iter_history():
        if e["n"] == n:
            return e
    return None
