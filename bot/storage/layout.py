"""Per-user vault layout and path helpers."""
from __future__ import annotations

import logging
from pathlib import Path

from .. import userctx
from ..atomic import atomic_write_text
from ..config import DOMAINS, LOG_PATH, PSYCHO_META_DIR
from .git import ensure_git_repo

log = logging.getLogger(__name__)


def raw_dir() -> Path:
    return userctx.user_root() / "00_raw" / "qna"


def profile_dir() -> Path:
    return userctx.user_root() / "02_profile"


def notes_dir() -> Path:
    return userctx.user_root() / "00_raw" / "notes"


def index_file() -> Path:
    return userctx.user_root() / "_index.md"


def state_file() -> Path:
    return userctx.user_root() / "_state.json"


def ensure_layout() -> None:
    """Создать структуру для текущего пользователя + глобальный `.psycho/`."""
    root = userctx.user_root()
    raw_dir().mkdir(parents=True, exist_ok=True)
    (root / "00_raw" / "sessions").mkdir(parents=True, exist_ok=True)
    notes_dir().mkdir(parents=True, exist_ok=True)
    (root / "01_mood" / "events").mkdir(parents=True, exist_ok=True)
    (root / "01_mood" / "analysis").mkdir(parents=True, exist_ok=True)
    (root / "01_mood" / "timeseries").mkdir(parents=True, exist_ok=True)
    (root / "02_concepts").mkdir(parents=True, exist_ok=True)
    (root / "02_digest").mkdir(parents=True, exist_ok=True)
    (root / "03_personality").mkdir(parents=True, exist_ok=True)
    profile_dir().mkdir(parents=True, exist_ok=True)
    PSYCHO_META_DIR.mkdir(parents=True, exist_ok=True)
    for domain in DOMAINS:
        f = profile_dir() / f"{domain}.md"
        if not f.exists():
            f.write_text(f"# Портрет: {domain}\n\n", encoding="utf-8")
    idx = index_file()
    if not idx.exists():
        lines = ["# Psycho — индекс", "", "## Портрет по темам", ""]
        lines += [f"- [[02_profile/{d}|{d}]]" for d in DOMAINS]
        lines += [
            "",
            "## Сырые логи",
            "",
            "`00_raw/sessions/` — полный event-log сессий.",
            "`00_raw/qna/` — человекочитаемая Q&A-проекция.",
        ]
        idx.write_text("\n".join(lines) + "\n", encoding="utf-8")
    if not LOG_PATH.exists():
        LOG_PATH.write_text("# Operation log\n\n", encoding="utf-8")
    try:
        from .. import about, mood_file
        about.ensure()
        mood_file.ensure()
    except Exception:
        log.exception("failed to ensure 03_personality/ files")
    _ensure_user_graph_settings()
    ensure_git_repo()


_GRAPH_TEMPLATE = Path(__file__).parents[1] / "assets" / "graph.json"


def _ensure_user_graph_settings() -> None:
    gj = userctx.user_root() / ".obsidian" / "graph.json"
    if gj.exists() or not _GRAPH_TEMPLATE.exists():
        return
    try:
        gj.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(gj, _GRAPH_TEMPLATE.read_text(encoding="utf-8"))
    except OSError:
        log.warning("could not seed graph.json for %s", userctx.user_root())

