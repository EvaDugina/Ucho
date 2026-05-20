import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

from .config import DOMAINS, VAULT_PATH

log = logging.getLogger(__name__)

RAW_DIR = VAULT_PATH / "raw"
PROFILE_DIR = VAULT_PATH / "profile"
INDEX_FILE = VAULT_PATH / "_index.md"
STATE_FILE = VAULT_PATH / "_state.json"

# Парсер записей вида:
#   ## Q42 · 14:32 · politics
#   **Q:** ...
#   **A:** ...
_ENTRY_RE = re.compile(
    r"##\s+Q(\d+)\s*[·\-—]\s*(\d{2}:\d{2})\s*[·\-—]\s*(\w+)\s*\n"
    r"\*\*Q:\*\*\s*(.*?)\n"
    r"\*\*A:\*\*\s*(.*?)(?=\n##\s+Q\d+\s*[·\-—]|\Z)",
    re.DOTALL,
)


def ensure_layout() -> None:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    for domain in DOMAINS:
        f = PROFILE_DIR / f"{domain}.md"
        if not f.exists():
            f.write_text(f"# Портрет: {domain}\n\n", encoding="utf-8")
    if not INDEX_FILE.exists():
        lines = ["# Psycho — индекс", "", "## Портрет по доменам", ""]
        lines += [f"- [[profile/{d}|{d}]]" for d in DOMAINS]
        lines += ["", "## Сырые логи", "", "Папка `raw/` — Q&A по дням."]
        INDEX_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------- сквозная нумерация вопросов ----------


def _load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            log.exception("failed to load state, resetting")
    return {"last_q_num": 0}


def _save_state(state: dict) -> None:
    ensure_layout()
    STATE_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def next_q_num() -> int:
    state = _load_state()
    state["last_q_num"] = int(state.get("last_q_num", 0)) + 1
    _save_state(state)
    return state["last_q_num"]


# ---------- запись ----------


def append_raw(q_num: int, when: datetime, domain: str, question: str, answer: str) -> Path:
    ensure_layout()
    date_str = when.strftime("%Y-%m-%d")
    time_str = when.strftime("%H:%M")
    path = RAW_DIR / f"{date_str}.md"
    block = (
        f"## Q{q_num} · {time_str} · {domain}\n"
        f"**Q:** {question}\n"
        f"**A:** {answer}\n\n"
    )
    if not path.exists():
        path.write_text(f"# {date_str}\n\n", encoding="utf-8")
    with path.open("a", encoding="utf-8") as f:
        f.write(block)
    return path


def append_profile(when: datetime, domain: str, fragment: str, raw_time: str) -> Path:
    if domain not in DOMAINS:
        raise ValueError(f"unknown domain: {domain}")
    ensure_layout()
    date_str = when.strftime("%Y-%m-%d")
    path = PROFILE_DIR / f"{domain}.md"
    block = (
        f"### {date_str}\n"
        f"- {fragment} _(из [[raw/{date_str}|{raw_time}]])_\n\n"
    )
    with path.open("a", encoding="utf-8") as f:
        f.write(block)
    return path


# ---------- чтение истории ----------


def iter_history() -> list[dict]:
    """Все записи Q&A по всем дням, отсортированные по Q-номеру по возрастанию.

    Каждая запись: {n, date, time, domain, question, answer}.
    """
    if not RAW_DIR.exists():
        return []
    entries: list[dict] = []
    for path in sorted(RAW_DIR.glob("*.md")):
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


def find_question(n: int) -> Optional[dict]:
    for e in iter_history():
        if e["n"] == n:
            return e
    return None
