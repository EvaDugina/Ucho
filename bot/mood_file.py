"""Живой черновик настроения пользователя: `personality/mood.md`.

Capture-first (как и весь проект): **код** пишет структурный снимок из классификации
Qwen (`moods.session_mood`/`mood_label`) — текущий вектор (эмоция/V/A/D/устойчивость),
последнее «лицо» Иуды, prior (`mood_baseline`). Это ЧЕРНОВИК; выверенный граф
настроений и baseline собирает **weekly-review** в папке `mood/`.

`mood_baseline` (prior для `moods.session_mood`) хранится здесь (раньше — в about).

Миграция: mood-поля из старого `about_user.md` лениво переносятся сюда в `ensure()`.
Старый файл бот не удаляет (инвариант) — чистят руками.
"""
import logging
import re
from datetime import datetime
from pathlib import Path

import yaml

from . import moods, userctx
from .atomic import atomic_write_text

log = logging.getLogger(__name__)

_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n(.*)$", re.DOTALL)
_MOOD_KEYS = ("mood", "bot_mood", "mood_baseline")


def _dir() -> Path:
    return userctx.user_root() / "personality"


def path() -> Path:
    return _dir() / "mood.md"


def _legacy_about() -> Path:
    return userctx.user_root() / "about_user.md"


def _legacy_mood_fields() -> dict:
    """Достать mood-поля из старого about_user.md (для миграции). Нет → {}."""
    legacy = _legacy_about()
    if not legacy.exists():
        return {}
    try:
        m = _FRONTMATTER_RE.match(legacy.read_text(encoding="utf-8"))
        if not m:
            return {}
        fm = yaml.safe_load(m.group(1)) or {}
        return {k: fm[k] for k in _MOOD_KEYS if isinstance(fm, dict) and fm.get(k)}
    except Exception:
        log.exception("read legacy mood fields failed (non-fatal)")
        return {}


def _compose(fm: dict, mood_vec: dict | None = None, bot_mood: str | None = None) -> str:
    head = yaml.safe_dump(fm, allow_unicode=True, sort_keys=False).strip()
    if mood_vec:
        line = (
            f"Текущий снимок по сессии: {mood_vec.get('quality', '—')} · "
            f"валентность {mood_vec.get('valence')} · энергия {mood_vec.get('energy')} · "
            f"доминирование {mood_vec.get('dominance_label')} · "
            f"устойчивость {mood_vec.get('stability')}. "
            f"Последнее лицо Иуды: {bot_mood or '—'}."
        )
    else:
        line = "Снимок ещё не считался."
    body = (
        "# Настроение (черновик)\n\n"
        f"{line}\n\n"
        "> Живой черновик: пишет код из категориальной классификации Qwen на каждый "
        "ответ. Выверенный граф настроений, карту лиц и `mood_baseline` собирает "
        "weekly-review в папке `mood/`.\n"
    )
    return f"---\n{head}\n---\n\n{body}"


def ensure() -> None:
    """Создать `personality/mood.md`, если его нет. Переносит mood-поля из legacy
    about_user.md (миграция). Идемпотентно."""
    p = path()
    if p.exists():
        return
    _dir().mkdir(parents=True, exist_ok=True)
    fm = {
        "updated": datetime.now().strftime("%Y-%m-%d"),
        "quality": "", "valence": "", "arousal": "", "dominance": "",
        "stability": "", "bot_mood": "", "mood_baseline": "", "n": 0,
    }
    fm.update(_legacy_mood_fields())  # перенос prior/последнего лица, если были
    atomic_write_text(p, _compose(fm))


def _parse() -> dict:
    p = path()
    if not p.exists():
        return {}
    try:
        m = _FRONTMATTER_RE.match(p.read_text(encoding="utf-8"))
        if not m:
            return {}
        fm = yaml.safe_load(m.group(1)) or {}
        return fm if isinstance(fm, dict) else {}
    except Exception:
        log.exception("mood.md parse failed (non-fatal)")
        return {}


def set_current(mood_vec: dict, bot_mood: str | None = None) -> None:
    """Записать текущий снимок настроения (живой черновик). `mood_baseline`
    (его пишет weekly-review) сохраняется. Никогда не роняет вызывающий код."""
    if not isinstance(mood_vec, dict):
        return
    try:
        ensure()
        fm = _parse()
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
        fm.setdefault("mood_baseline", "")  # не затираем prior от weekly
        atomic_write_text(path(), _compose(fm, mood_vec, bot_mood or fm.get("bot_mood")))
    except Exception:
        log.exception("mood_file.set_current failed (non-fatal)")


def baseline() -> tuple[float, float, float]:
    """Prior (valence, arousal, dominance) из `mood_baseline` (пишет weekly).
    Формат "v,a,d", back-compat "v,a" → d=0. Нет mood.md → пробуем legacy about.
    Нет/мусор → (0,0,0)."""
    def _clamp(x: str) -> float:
        return max(-1.0, min(1.0, float(x)))
    try:
        fm = _parse()
        raw = str(fm.get("mood_baseline") or "").strip()
        if not raw:
            raw = str(_legacy_mood_fields().get("mood_baseline") or "").strip()
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
    fm = _parse()
    parts = []
    if str(fm.get("mood") or "").strip():
        parts.append(f"настроение: {fm['mood']}")
    if str(fm.get("bot_mood") or "").strip():
        parts.append(f"последнее лицо: {fm['bot_mood']}")
    return "; ".join(parts)
