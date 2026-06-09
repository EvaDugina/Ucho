"""Живой черновик настроения пользователя: `01_Мироощущение/mood/mood.md`.

Capture-first (как и весь проект), разделение как в `about.py`:
- **Код (live, каждый ход)** пишет ТОЛЬКО frontmatter — живой снимок из классификации
  LLM (`moods`): эмоция/V/A/D/устойчивость/последнее лицо. Тело файла НЕ трогает.
- **Скилл `depersonalization` (вручную)** пишет в ТЕЛЕ связный анализ настроения за
  период (доминанты, динамика, триггеры) + ставит `mood_baseline` (prior) и строит
  выверенный граф настроений в `01_Мироощущение/mood/`. Код тело сохраняет — нарратив скилла не затирается.

`mood_baseline` (prior для `moods.session_mood`) хранится здесь.
"""
import logging
import re
from datetime import datetime
from pathlib import Path

import yaml

from . import moods, vault
from .atomic import atomic_write_text

log = logging.getLogger(__name__)

_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n(.*)$", re.DOTALL)

# Тело-скелет: H1 + пояснение + пустая секция анализа (её пишет depersonalization).
_BODY = (
    "# Настроение\n\n"
    "> Frontmatter выше — живой снимок настроения (пишет код на каждый ответ из "
    "категориальной классификации LLM). Связный анализ ниже выверяет и пишет "
    "скилл `depersonalization`; выверенный граф настроений — в папке `01_Мироощущение/mood/`.\n\n"
    "## Анализ настроения\n\n"
)


def _dir() -> Path:
    return vault.mood_dir()


def path() -> Path:
    return _dir() / "mood.md"


def _write(fm: dict, body: str) -> None:
    head = yaml.safe_dump(fm, allow_unicode=True, sort_keys=False).strip()
    content = f"---\n{head}\n---\n{body}"
    if not content.endswith("\n"):
        content += "\n"
    atomic_write_text(path(), content)


def ensure() -> None:
    """Создать пустой скелет `01_Мироощущение/mood/mood.md`, если его нет (идемпотентно)."""
    p = path()
    if p.exists():
        return
    _dir().mkdir(parents=True, exist_ok=True)
    fm = {
        "updated": datetime.now().strftime("%Y-%m-%d"),
        "quality": "", "valence": "", "arousal": "", "dominance": "",
        "stability": "", "bot_mood": "", "mood_baseline": "", "n": 0,
    }
    _write(fm, "\n" + _BODY)


def _parse() -> tuple[dict, str]:
    """(frontmatter dict, body). Нет файла/шапки → ({}, '')."""
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
        log.exception("mood.md frontmatter parse failed")
        fm = {}
    return (fm if isinstance(fm, dict) else {}), m.group(2)


def set_current(mood_vec: dict, bot_mood: str | None = None) -> None:
    """Записать живой снимок настроения в frontmatter. **Тело (анализ от
    depersonalization) НЕ трогаем** — иначе затёрли бы нарратив скилла.
    `mood_baseline` (его пишет скилл) сохраняется. Никогда не роняет вызывающий код."""
    if not isinstance(mood_vec, dict):
        return
    try:
        ensure()
        fm, body = _parse()
        fm["updated"] = datetime.now().strftime("%Y-%m-%d")
        fm["quality"] = mood_vec.get("quality")
        fm["valence"] = mood_vec.get("valence")
        fm["arousal"] = mood_vec.get("arousal")
        fm["dominance"] = mood_vec.get("dominance")
        fm["stability"] = mood_vec.get("stability")
        fm["n"] = mood_vec.get("n")
        fm["mood"] = moods.mood_label(mood_vec)
        if bot_mood:
            fm["bot_mood"] = str(bot_mood)
        fm.setdefault("mood_baseline", "")  # не затираем prior от скилла
        _write(fm, body)
    except Exception:
        log.exception("mood_file.set_current failed (non-fatal)")


def baseline() -> tuple[float, float, float]:
    """Prior (valence, arousal, dominance) из `mood_baseline` (пишет скилл).
    Формат "v,a,d", back-compat "v,a" → d=0. Нет/мусор → (0,0,0)."""
    def _clamp(x: str) -> float:
        return max(-1.0, min(1.0, float(x)))
    try:
        fm, _ = _parse()
        raw = str(fm.get("mood_baseline") or "").strip()
        if not raw:
            return (0.0, 0.0, 0.0)
        parts = [p.strip() for p in raw.split(",")]
        v = _clamp(parts[0])
        a = _clamp(parts[1]) if len(parts) > 1 else 0.0
        d = _clamp(parts[2]) if len(parts) > 2 else 0.0
        return (v, a, d)
    except Exception:
        return (0.0, 0.0, 0.0)


def render_for_prompt() -> str:
    """Короткая строка настроения для системного промпта персоны ('' если пусто)."""
    fm, _ = _parse()
    parts = []
    if str(fm.get("mood") or "").strip():
        parts.append(f"настроение: {fm['mood']}")
    if str(fm.get("bot_mood") or "").strip():
        parts.append(f"последнее лицо: {fm['bot_mood']}")
    return "; ".join(parts)
