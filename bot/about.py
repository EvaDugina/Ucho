"""Портрет пользователя (per-user): `personality/about.md` + журнал дельт.

Гибрид (capture-first):
- **Live (Qwen, каждый ответ).** В `mode: process` LLM отдаёт дешёвую `user_delta`
  (см. `prompts/process.md`). Код обновляет машинные поля frontmatter
  (`register/tone/openness/provocation_tolerance`), бампит `messages_seen`/`updated`
  и дописывает сырую дельту в `_user_deltas.jsonl`. **Прозу 14 секций live НЕ трогаем.**
- **Weekly (Claude).** Скилл `depersonalization` раз в неделю переписывает прозу секций
  из накопленных дельт + `raw/` (Qwen 14B для связного портрета слаба).

Настроение вынесено в `personality/mood.md` (см. `bot/mood_file.py`) — здесь только
портрет носителя, без mood-полей.

Миграция: старый `about_user.md` в корне per-user лениво переносится в
`personality/about.md` (mood-поля уезжают в mood.md через `mood_file`). Бот старый файл
**не удаляет** (инвариант проекта) — его чистят руками в Obsidian.

Файл инъецируется в системный промпт (`llm._system`). Пути — per-user через `userctx`.
Любой сбой здесь не должен ронять обработку ответа — отсюда широкие try/except.
"""
import json
import logging
import re
from datetime import datetime
from pathlib import Path

import yaml

from . import userctx
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
# (доверие/контроль, тянется/отстраняется); routine — уклад/быт (порядок vs хаос).
_PROSE_KEYS = (
    "speech_note", "trigger", "motif", "fact", "rapport",
    "style", "passion", "letdown",
    "epistemics", "attachment", "routine",
)
# mood-поля больше НЕ живут в about — они в personality/mood.md (bot/mood_file.py).
_LEGACY_MOOD_KEYS = ("mood", "bot_mood", "mood_baseline")

# 14 секций портрета (порядок фиксирован; прозу заполняет depersonalization).
_SECTIONS = (
    "Манера речи",
    "Стиль",
    "Характер",
    "Эпистемический стиль",
    "Привязанность и дистанция",
    "Ритуалы и быт",
    "Self-image vs зазор",
    "Болевые точки",
    "Сквозные мотивы",
    "Страсти (что вдохновляет)",
    "Огорчает / разочаровывает",
    "Общее",
    "Состояние диалога",
    "Эмоциональные реакции",
)


def _dir() -> Path:
    return userctx.user_root() / "personality"


def path() -> Path:
    return _dir() / "about.md"


def _legacy_path() -> Path:
    """Старое расположение портрета (до перехода на personality/)."""
    return userctx.user_root() / "about_user.md"


def _deltas_path() -> Path:
    return userctx.user_root() / "_user_deltas.jsonl"


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


def _migrate_from_legacy(legacy: Path) -> None:
    """Перенести содержимое legacy `about_user.md` в `personality/about.md`,
    выкинув mood-поля (их забирает mood_file). Старый файл НЕ удаляем."""
    try:
        m = _FRONTMATTER_RE.match(legacy.read_text(encoding="utf-8"))
    except Exception:
        log.exception("read legacy about failed")
        m = None
    if not m:
        atomic_write_text(path(), _skeleton())
        return
    try:
        fm = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError:
        fm = {}
    if not isinstance(fm, dict):
        fm = {}
    for k in _LEGACY_MOOD_KEYS:
        fm.pop(k, None)
    body = m.group(2)
    head = yaml.safe_dump(fm, allow_unicode=True, sort_keys=False).strip()
    content = f"---\n{head}\n---\n{body}"
    if not content.endswith("\n"):
        content += "\n"
    atomic_write_text(path(), content)


def ensure() -> None:
    """Создать `personality/about.md`, если его ещё нет (идемпотентно).

    Если есть старый `about_user.md` — лениво мигрируем его содержимое. Иначе —
    пустой скелет. Старый файл не трогаем (чистка — руками)."""
    p = path()
    if p.exists():
        return
    _dir().mkdir(parents=True, exist_ok=True)
    legacy = _legacy_path()
    if legacy.exists():
        _migrate_from_legacy(legacy)
    else:
        atomic_write_text(p, _skeleton())


def _parse() -> tuple[dict, str]:
    """(frontmatter dict, body). Нет нового файла → читаем legacy (back-compat),
    пока миграция не прошла. Совсем ничего → ({}, '')."""
    p = path()
    if not p.exists():
        legacy = _legacy_path()
        if legacy.exists():
            p = legacy
        else:
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
    """Сохранить непустые поля дельты строкой в `_user_deltas.jsonl` (кольцо)."""
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
