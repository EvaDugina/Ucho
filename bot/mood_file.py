"""Текущий компактный снимок настроения в ``01_mood/current.md``."""
from __future__ import annotations

import logging
import re
from pathlib import Path

from . import vault
from .atomic import atomic_write_text

log = logging.getLogger(__name__)


def path() -> Path:
    return vault.mood_dir() / "current.md"


def ensure() -> None:
    vault.mood_dir().mkdir(parents=True, exist_ok=True)


def set_current(mv: dict) -> None:
    ensure()
    try:
        content = (
            "---\n"
            f"valence: {float(mv.get('valence', 0.0)):.3f}\n"
            f"arousal: {float(mv.get('arousal', 0.0)):.3f}\n"
            f"dominance: {float(mv.get('dominance', 0.0)):.3f}\n"
            f"sign: {mv.get('sign', '0')}\n"
            f"energy: {mv.get('energy', 'normal')}\n"
            f"quality: {mv.get('quality', 'спокойствие')}\n"
            f"direction: {mv.get('direction', 'neutral')}\n"
            f"stability: {mv.get('stability', 'adequate')}\n"
            f"n: {int(mv.get('n', 0))}\n"
            "---\n\n"
            "# Текущее настроение\n\n"
            "Автоматический live-снимок последней активной сессии.\n"
        )
        atomic_write_text(path(), content)
    except Exception:
        log.exception("mood current update failed (non-fatal)")


def baseline() -> tuple[float, float, float]:
    if not path().exists():
        return 0.0, 0.0, 0.0
    try:
        text = path().read_text(encoding="utf-8")
        values = []
        for key in ("valence", "arousal", "dominance"):
            match = re.search(rf"^{key}:\s*(-?\d+(?:\.\d+)?)", text, re.MULTILINE)
            values.append(float(match.group(1)) if match else 0.0)
        return tuple(values)  # type: ignore[return-value]
    except Exception:
        log.exception("mood current parse failed")
        return 0.0, 0.0, 0.0


def render_for_prompt(max_chars: int = 1200) -> str:
    if not path().exists():
        return ""
    try:
        return path().read_text(encoding="utf-8")[-max_chars:]
    except OSError:
        return ""
