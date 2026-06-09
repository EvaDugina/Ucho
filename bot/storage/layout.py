"""Per-user vault layout and path helpers."""
from __future__ import annotations

import logging
from pathlib import Path

from .. import userctx
from ..atomic import atomic_write_text
from ..config import LOG_PATH, PSYCHO_META_DIR
from ..worldview_taxonomy import GENERAL_FOLDER, WORLDVIEW_AREAS
from .git import ensure_git_repo

log = logging.getLogger(__name__)


def raw_dir() -> Path:
    return userctx.user_root() / "00_raw" / "qna"


def profile_dir() -> Path:
    """Legacy helper. New short summary lives in `05_Общее/summary.md`."""
    return userctx.user_root() / GENERAL_FOLDER


def general_dir() -> Path:
    return userctx.user_root() / GENERAL_FOLDER


def worldview_area_dir(area_key: str) -> Path:
    for area in WORLDVIEW_AREAS:
        if area.key == area_key or area.folder == area_key:
            return userctx.user_root() / area.folder
    raise ValueError(f"unknown worldview area: {area_key}")


def worldview_atoms_dir(area_key: str) -> Path:
    return worldview_area_dir(area_key) / "atoms"


def mood_dir() -> Path:
    return userctx.user_root() / "01_Мироощущение" / "mood"


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
    for area in WORLDVIEW_AREAS:
        area_root = root / area.folder
        (area_root / "atoms").mkdir(parents=True, exist_ok=True)
        moc = area_root / "MOC.md"
        if not moc.exists():
            moc.write_text(
                "\n".join([
                    "---",
                    "type: worldview-moc",
                    f"area: {area.key}",
                    f"area_folder: {area.folder}",
                    "---",
                    "",
                    f"# {area.folder}",
                    "",
                    area.description,
                    "",
                    "_Пока ни одного атома._",
                    "",
                ]),
                encoding="utf-8",
            )
    (mood_dir() / "events").mkdir(parents=True, exist_ok=True)
    (mood_dir() / "analysis").mkdir(parents=True, exist_ok=True)
    (mood_dir() / "timeseries").mkdir(parents=True, exist_ok=True)
    general_dir().mkdir(parents=True, exist_ok=True)
    PSYCHO_META_DIR.mkdir(parents=True, exist_ok=True)
    summary = general_dir() / "summary.md"
    if not summary.exists():
        summary.write_text(
            "# Общее резюме\n\n"
            "_Краткая сводка о пользователе и сжатые выводы по 01-04. "
            "Заполняет сильная модель._\n",
            encoding="utf-8",
        )
    idx = index_file()
    if not idx.exists():
        lines = ["# Psycho — индекс", "", "## Мировоззрение", ""]
        lines += [f"- [[{a.folder}/MOC|{a.folder}]]" for a in WORLDVIEW_AREAS]
        lines += ["- [[05_Общее/summary|05_Общее]]"]
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
        log.exception("failed to ensure 05_Общее/mood files")
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
