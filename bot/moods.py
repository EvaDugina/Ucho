"""Категориальное настроение и его помесячный журнал."""
from __future__ import annotations

import json
import logging
from datetime import datetime

from . import vault
from .atomic import atomic_write_text

log = logging.getLogger(__name__)

SIGNS = ("+", "0", "-")
ENERGY = ("high", "normal", "low")
DIRECTION = ("auto", "hetero", "neutral")
DOMINANCE = ("high", "normal", "low")
QUALITIES = (
    "тревога",
    "страх",
    "грусть_тоска",
    "апатия_подавленность",
    "раздражение_гнев",
    "стыд_вина",
    "спокойствие",
    "сосредоточенность",
    "радость",
    "воодушевление_азарт",
    "гордость_самоуверенность",
    "презрение_зависть",
)
_RECENCY_DECAY = 0.6


def normalize_per_msg(value: object) -> dict:
    data = value if isinstance(value, dict) else {}
    return {
        "sign": data.get("sign") if data.get("sign") in SIGNS else "0",
        "energy": data.get("energy") if data.get("energy") in ENERGY else "normal",
        "direction": (
            data.get("direction") if data.get("direction") in DIRECTION else "neutral"
        ),
        "quality": (
            data.get("quality") if data.get("quality") in QUALITIES else "спокойствие"
        ),
        "dominance": (
            data.get("dominance") if data.get("dominance") in DOMINANCE else "normal"
        ),
    }


def _to_numeric(per_msg: dict) -> tuple[int, int, int]:
    item = normalize_per_msg(per_msg)
    return (
        {"+": 1, "0": 0, "-": -1}[item["sign"]],
        {"high": 1, "normal": 0, "low": -1}[item["energy"]],
        {"high": 1, "normal": 0, "low": -1}[item["dominance"]],
    )


def _axis_label(value: float, positive: str, neutral: str, negative: str) -> str:
    return positive if value > 0.33 else negative if value < -0.33 else neutral


def session_mood(
    trajectory: list[dict],
    prior: tuple[float, float, float] = (0.0, 0.0, 0.0),
) -> dict:
    items = [normalize_per_msg(item) for item in trajectory if isinstance(item, dict)]
    if not items:
        valence, arousal, dominance = prior
        return {
            "valence": valence,
            "arousal": arousal,
            "dominance": dominance,
            "sign": _axis_label(valence, "+", "0", "-"),
            "energy": _axis_label(arousal, "high", "normal", "low"),
            "dominance_label": _axis_label(dominance, "high", "normal", "low"),
            "quality": "спокойствие",
            "direction": "neutral",
            "stability": "adequate",
            "n": 0,
        }

    weights = [_RECENCY_DECAY ** (len(items) - 1 - index) for index in range(len(items))]
    numeric = [_to_numeric(item) for item in items]
    total = sum(weights) or 1.0
    values = [
        sum(
            row[axis] * weight
            for row, weight in zip(numeric, weights, strict=True)
        )
        / total
        for axis in range(3)
    ]
    prior_weight = 2.0 / (2.0 + len(items))
    values = [
        prior_weight * prior[index] + (1 - prior_weight) * values[index]
        for index in range(3)
    ]
    raw_valence = [row[0] for row in numeric]
    mean = sum(raw_valence) / len(raw_valence)
    variance = sum((value - mean) ** 2 for value in raw_valence) / len(raw_valence)
    recent = items[-3:]
    quality = max(
        QUALITIES,
        key=lambda candidate: sum(
            index + 1
            for index, item in enumerate(recent)
            if item["quality"] == candidate
        ),
    )
    valence, arousal, dominance = [
        round(max(-1.0, min(1.0, value)), 3) for value in values
    ]
    return {
        "valence": valence,
        "arousal": arousal,
        "dominance": dominance,
        "sign": _axis_label(valence, "+", "0", "-"),
        "energy": _axis_label(arousal, "high", "normal", "low"),
        "dominance_label": _axis_label(dominance, "high", "normal", "low"),
        "quality": quality,
        "direction": items[-1]["direction"],
        "stability": (
            "rigid" if variance < 0.15 else "labile" if variance > 0.75 else "adequate"
        ),
        "n": len(items),
    }


def _event_datetime(value: object | None) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            pass
    return datetime.now()


def _event_already_logged(raw_event_id: str) -> bool:
    events_dir = vault.mood_dir() / "events"
    if not events_dir.exists():
        return False
    marker = f'"raw_event_id": "{raw_event_id}"'
    for path in events_dir.glob("*.jsonl"):
        try:
            if marker in path.read_text(encoding="utf-8"):
                return True
        except OSError:
            log.exception("mood event scan failed: %s", path)
    return False


def log_turn(
    mood_vec: dict,
    *,
    raw_event_id: str,
    session_id: str | None = None,
    q_num: int | None = None,
    at: object | None = None,
) -> bool:
    """Записать ровно одно mood-событие для raw event."""
    if not raw_event_id or _event_already_logged(raw_event_id):
        return False
    try:
        occurred_at = _event_datetime(at)
        entry = {
            "ts": occurred_at.isoformat(timespec="seconds"),
            "raw_event_id": raw_event_id,
            "session_id": session_id,
            "q_num": q_num,
            **{
                key: mood_vec.get(key)
                for key in (
                    "sign",
                    "energy",
                    "direction",
                    "quality",
                    "valence",
                    "arousal",
                    "dominance",
                    "dominance_label",
                    "stability",
                    "n",
                )
            },
        }
        path = vault.mood_dir() / "events" / f"{occurred_at:%Y-%m}.jsonl"
        lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
        lines.append(json.dumps(entry, ensure_ascii=False))
        atomic_write_text(path, "\n".join(lines) + "\n")
        return True
    except Exception:
        log.exception("mood event write failed (non-fatal)")
        return False
