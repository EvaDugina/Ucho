"""Портрет пользователя (per-user): `05_Общее/about.md` + журнал дельт.

Гибрид (capture-first):
- **Live (OpenAI-compatible provider, каждый ответ).** В `mode: process` LLM отдаёт дешёвую `user_delta`
  (см. `prompts/process.md`). Код обновляет машинные поля frontmatter
  (`register/tone/openness/provocation_tolerance`), бампит `messages_seen`/`updated`
  и дописывает сырую дельту в `05_Общее/deltas.jsonl`. **Прозу 20 секций live НЕ трогаем.**
- **Manual strong pass.** Скилл `depersonalization` переписывает прозу секций
  из накопленных дельт + `00_raw/`; live-модель связную прозу 20 секций не ведёт.

Настроение вынесено в `01_Мироощущение/mood/mood.md` (см. `bot/mood_file.py`) — здесь только
портрет носителя, без mood-полей.

Файл инъецируется в системный промпт (`llm._system`). Пути — per-user через `userctx`.
Любой сбой здесь не должен ронять обработку ответа — отсюда широкие try/except.
"""
import json
import logging
import re
from datetime import datetime
from pathlib import Path

import yaml

from . import vault
from .atomic import atomic_write_text

log = logging.getLogger(__name__)

_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n(.*)$", re.DOTALL)
_DELTAS_MAX = 200  # кольцо журнала дельт (старое вытесняется; чистит depersonalization)

# Машинные поля frontmatter, которые live-дельта обновляет (overwrite-if-present).
_FIELD_KEYS = ("register", "tone", "openness", "provocation_tolerance")
# Прозаические поля дельты — копятся в журнал, прозу пишет depersonalization.
# style — стиль/вкус/самоподача; passion — что вдохновляет/зажигает;
# letdown — что огорчает/разочаровывает; epistemics — как познаёт (доверие
# опыту/логике/авторитету, перенос «не знаю»); attachment — как строит близость
# (доверие/контроль, тянется/отстраняется); routine — уклад/быт (порядок vs хаос);
# limits — что нерушимо vs торгуемо (этич. границы); power — отношение к власти/иерархии;
# selfhood — на чём держится «я» (опоры самости); finitude — отношение к конечности/времени;
# roots — корни/принадлежность (свой-чужой); vocation — что для него значит «дело»/труд.
_PROSE_KEYS = (
    "speech_note", "trigger", "motif", "fact", "rapport",
    "style", "passion", "letdown",
    "epistemics", "attachment", "routine",
    "limits", "power", "selfhood", "finitude", "roots", "vocation",
)
# Примечание: mood-поля живут в 01_Мироощущение/mood/mood.md (bot/mood_file.py), не здесь.

# 20 секций портрета (порядок фиксирован; прозу заполняет depersonalization).
_SECTIONS = (
    "Манера речи",
    "Стиль",
    "Характер",
    "Эпистемический стиль",
    "Привязанность и дистанция",
    "Ритуалы и быт",
    "Self-image vs зазор",
    "Опоры самости",
    "Болевые точки",
    "Линии, которые не переходит",
    "Сквозные мотивы",
    "Отношение к власти и иерархии",
    "Корни и принадлежность",
    "Что значит дело",
    "Конечность и время",
    "Страсти (что вдохновляет)",
    "Огорчает / разочаровывает",
    "Общее",
    "Состояние диалога",
    "Эмоциональные реакции",
)


def _dir() -> Path:
    return vault.general_dir()


def path() -> Path:
    return _dir() / "about.md"


def _deltas_path() -> Path:
    return _dir() / "deltas.jsonl"


def _skeleton() -> str:
    fm = {
        "updated": datetime.now().strftime("%Y-%m-%d"),
        "messages_seen": 0,
        "register": "",
        "tone": "",
        "openness": "",
        "provocation_tolerance": "",
    }
    head = yaml.safe_dump(fm, allow_unicode=True, sort_keys=False).strip()
    body = "\n".join(f"## {s}\n" for s in _SECTIONS)
    return f"---\n{head}\n---\n\n# Портрет пользователя\n\n{body}\n"


def ensure() -> None:
    """Создать пустой скелет `05_Общее/about.md`, если его ещё нет (идемпотентно)."""
    p = path()
    if p.exists():
        return
    _dir().mkdir(parents=True, exist_ok=True)
    atomic_write_text(p, _skeleton())


def _parse() -> tuple[dict, str]:
    """(frontmatter dict, body). Нет файла → ({}, '')."""
    p = path()
    if not p.exists():
        return {}, ""
    text = p.read_text(encoding="utf-8")
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}, text
    try:
        fm = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError:
        log.exception("about frontmatter parse failed")
        fm = {}
    return (fm if isinstance(fm, dict) else {}), m.group(2)


def _parse_markdown(text: str) -> tuple[dict, str]:
    """Разобрать markdown с optional YAML frontmatter."""
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}, text
    try:
        fm = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError:
        log.exception("personality markdown frontmatter parse failed")
        fm = {}
    return (fm if isinstance(fm, dict) else {}), m.group(2)


def _read_personality_markdown(name: str) -> tuple[dict, str]:
    p = _dir() / name
    if not p.exists():
        return {}, ""
    try:
        return _parse_markdown(p.read_text(encoding="utf-8"))
    except Exception:
        log.exception("failed to read personality markdown: %s", name)
        return {}, ""


def _clip(text: str, max_chars: int) -> str:
    text = text.strip()
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "…"


def _mood_context(max_chars: int = 1800) -> str:
    from . import mood_file

    p = mood_file.path()
    if not p.exists():
        return ""
    try:
        fm, body = _parse_markdown(p.read_text(encoding="utf-8"))
    except Exception:
        log.exception("failed to read mood markdown: %s", p)
        return ""
    summary_keys = ("mood", "quality", "valence", "arousal", "dominance", "stability", "bot_mood")
    summary = [f"{k}: {fm[k]}" for k in summary_keys if str(fm.get(k) or "").strip()]

    lines: list[str] = []
    for raw in body.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line == "# Настроение":
            continue
        if line.startswith("> Frontmatter выше"):
            continue
        lines.append(line)
    narrative = "\n".join(lines).strip()
    if narrative == "## Анализ настроения":
        narrative = ""

    parts = []
    if summary:
        parts.append("; ".join(summary))
    if narrative:
        parts.append(narrative)
    return _clip("\n".join(parts), max_chars)


def _profile_context(max_chars: int = 2200) -> str:
    _, body = _read_personality_markdown("profile.md")
    lines = [ln.rstrip() for ln in body.splitlines()]
    text = "\n".join(lines).strip()
    return _clip(text, max_chars)


def render_about_context(max_chars: int = 6500) -> str:
    """Контекст для `/about`: портрет + настроение + psych-profile, если есть.

    `about.md` остаётся главным источником, но публичный портрет получает ещё
    выверенный нарратив настроения и профиль личности, которые пишет
    depersonalization. Пустые скелеты не считаем содержательным контекстом.
    """
    parts: list[str] = []
    portrait = render_for_prompt(max_chars=2500)
    if portrait:
        parts.append("# Портрет\n" + portrait)
    mood = _mood_context()
    if mood:
        parts.append("# Настроение\n" + mood)
    profile = _profile_context()
    if profile:
        parts.append("# Психометрический профиль\n" + profile)
    return _clip("\n\n".join(parts), max_chars)


def apply_delta(delta: dict) -> None:
    """Дёшево применить live-дельту LLM. Никогда не роняет вызывающий код."""
    if not isinstance(delta, dict):
        return
    try:
        ensure()
        fm, body = _parse()
        for k in _FIELD_KEYS:
            v = delta.get(k)
            if isinstance(v, str) and v.strip():
                fm[k] = v.strip()
        fm["messages_seen"] = int(fm.get("messages_seen") or 0) + 1
        fm["updated"] = datetime.now().strftime("%Y-%m-%d")
        head = yaml.safe_dump(fm, allow_unicode=True, sort_keys=False).strip()
        content = f"---\n{head}\n---\n{body}"
        if not content.endswith("\n"):
            content += "\n"
        atomic_write_text(path(), content)
        _append_delta_log(delta)
    except Exception:
        log.exception("about.apply_delta failed (non-fatal)")


def _append_delta_log(delta: dict) -> None:
    """Сохранить непустые поля дельты строкой в `05_Общее/deltas.jsonl` (кольцо)."""
    kept = {
        k: delta[k].strip()
        for k in (*_FIELD_KEYS, *_PROSE_KEYS)
        if isinstance(delta.get(k), str) and delta[k].strip()
    }
    if not kept:
        return
    p = _deltas_path()
    lines = p.read_text(encoding="utf-8").splitlines() if p.exists() else []
    entry = {"ts": datetime.now().isoformat(timespec="seconds"), **kept}
    lines.append(json.dumps(entry, ensure_ascii=False))
    atomic_write_text(p, "\n".join(lines[-_DELTAS_MAX:]) + "\n")


def render_for_prompt(max_chars: int = 1500) -> str:
    """Компактный портрет для системного промпта. '' если пусто/нет файла.

    Пустые секции (прозу пишет depersonalization) опускаем — на ранней стадии в промпт
    уходит только строка машинных полей, чтобы не жечь токены пустыми заголовками.
    Настроение инъецируется отдельно (см. `llm._portrait_block` + `mood_file`)."""
    fm, body = _parse()
    fields = [f"{k}: {fm[k]}" for k in _FIELD_KEYS if str(fm.get(k) or "").strip()]

    sections: list[str] = []
    title: str | None = None
    buf: list[str] = []

    def flush() -> None:
        content = " ".join(ln.strip() for ln in buf if ln.strip()).strip()
        if title and content:
            sections.append(f"{title}: {content}")

    for line in body.splitlines():
        if line.startswith("## "):
            flush()
            title = line[3:].strip()
            buf = []
        elif line.startswith("#"):
            continue  # H1 заголовок файла
        else:
            buf.append(line)
    flush()

    parts: list[str] = []
    if fields:
        parts.append("; ".join(fields))
    parts.extend(sections)
    out = "\n".join(parts).strip()
    if len(out) > max_chars:
        out = out[:max_chars].rstrip() + "…"
    return out
