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

log = logging.getLogger(__name__)

_ENTRY_RE = re.compile(
    r"##\s+Q(\d+)\s*[·\-—]\s*(\d{2}:\d{2})\s*[·\-—]\s*(\w+)\s*\n"
    r"\*\*Q:\*\*\s*(.*?)\n"
    r"\*\*A:\*\*\s*(.*?)(?=\n##\s+Q\d+\s*[·\-—]|\Z)",
    re.DOTALL,
)


def append_raw(q_num: int, when: datetime, domain: str, question: str, answer: str) -> Path:
    layout.ensure_layout()
    if domain not in DOMAINS and domain != "user":
        append_log("warn", "append_raw_unknown_domain", f"q={q_num} domain={domain!r}")
    date_str = when.strftime("%Y-%m-%d")
    time_str = when.strftime("%H:%M")
    path = layout.raw_dir() / f"{date_str}.md"

    q_clean = escape_raw_block(safe_question_text(question))
    a_clean, a_truncated = safe_user_text(answer)
    if a_truncated:
        append_log("warn", "raw_answer_truncated", f"Q{q_num} length>limit")
    a_clean = escape_raw_block(a_clean)
    block = (
        f"## Q{q_num} · {time_str} · {domain}\n"
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
            entries.append({
                "n": int(m.group(1)),
                "date": date_str,
                "time": m.group(2),
                "domain": m.group(3),
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

