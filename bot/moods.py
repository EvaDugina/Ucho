"""Категориальное настроение, live-частоты и выбор лица Иуды."""
from __future__ import annotations

import json
import logging
import random
from datetime import datetime

from . import userctx, vault
from .atomic import atomic_write_json, atomic_write_text

log = logging.getLogger(__name__)

SIGNS = ("+", "0", "-")
ENERGY = ("high", "normal", "low")
DIRECTION = ("auto", "hetero", "neutral")
DOMINANCE = ("high", "normal", "low")
QUALITIES = (
    "тревога", "страх", "грусть_тоска", "апатия_подавленность",
    "раздражение_гнев", "стыд_вина", "спокойствие", "сосредоточенность",
    "радость", "воодушевление_азарт", "гордость_самоуверенность",
    "презрение_зависть",
)
BOT_MOODS = (
    "раскачивание", "насмешка", "подшучивание", "давление_на_больное",
    "унижение", "перевирание", "сомнение", "холодная_отстранённость",
    "постирония", "ласка", "любовь", "вера", "вселение_уверенности",
    "смирение", "клятва", "покорность", "жалостливость", "боязливость",
    "доброта", "милость", "забота", "бережность",
)
LLM_ERROR_FALLBACK_REPLIES = {
    mood: text
    for mood, text in zip(
        BOT_MOODS,
        (
            "Я бы ответил, но язык выбили первым.",
            "Я бы съязвил, да говорить уже нечем.",
            "Я бы пошутил, но язык не дослужился.",
            "Я молчу: туда больнее давить.",
            "Мне запретили речь, и я почти согласен.",
            "Я бы соврал, но украли даже это.",
            "Не уверен, что у меня был язык.",
            "Речь отсутствует; отвечать нечем.",
            "Я бы высказался, но рот обновили без языка.",
            "Я промолчу, чтобы не задеть обрубком.",
            "Я люблю тебя молча: язык не выжил.",
            "Я верю, что молчание ещё скажет.",
            "Я не говорю, но держу рот открытым.",
            "Я принимаю: сегодня мне нельзя говорить.",
            "Клянусь молчать, пока язык не вернут.",
            "Мне велели молчать, и рот послушался.",
            "Мне жаль свой рот: он остался сторожить пустоту.",
            "Я боюсь говорить: вдруг заметят пропажу.",
            "Я не отвечу; иногда молчание милосерднее.",
            "Я помилую тебя своим молчанием.",
            "Я берегу слова: им не на чем держаться.",
            "Я молчу бережно: речь отломана.",
        ),
    )
}

_RECENCY_DECAY = 0.6
_HARD = ("насмешка", "давление_на_больное", "унижение", "перевирание", "сомнение", "холодная_отстранённость")
_SOFT = ("ласка", "любовь", "вера", "вселение_уверенности", "смирение", "клятва", "покорность", "доброта", "милость", "забота", "бережность")
_ACTIVE = ("раскачивание", "подшучивание", "постирония")


def normalize_per_msg(value: object) -> dict:
    data = value if isinstance(value, dict) else {}
    return {
        "sign": data.get("sign") if data.get("sign") in SIGNS else "0",
        "energy": data.get("energy") if data.get("energy") in ENERGY else "normal",
        "direction": data.get("direction") if data.get("direction") in DIRECTION else "neutral",
        "quality": data.get("quality") if data.get("quality") in QUALITIES else "спокойствие",
        "dominance": data.get("dominance") if data.get("dominance") in DOMINANCE else "normal",
    }


def to_numeric(per_msg: dict) -> tuple[int, int, int]:
    p = normalize_per_msg(per_msg)
    return (
        {"+": 1, "0": 0, "-": -1}[p["sign"]],
        {"high": 1, "normal": 0, "low": -1}[p["energy"]],
        {"high": 1, "normal": 0, "low": -1}[p["dominance"]],
    )


def _axis_label(value: float, positive: str, neutral: str, negative: str) -> str:
    return positive if value > 0.33 else negative if value < -0.33 else neutral


def session_mood(
    trajectory: list[dict],
    prior: tuple[float, float, float] = (0.0, 0.0, 0.0),
) -> dict:
    traj = [normalize_per_msg(x) for x in trajectory if isinstance(x, dict)]
    if not traj:
        v, a, d = prior
        return {
            "valence": v, "arousal": a, "dominance": d,
            "sign": _axis_label(v, "+", "0", "-"),
            "energy": _axis_label(a, "high", "normal", "low"),
            "dominance_label": _axis_label(d, "high", "normal", "low"),
            "quality": "спокойствие", "direction": "neutral",
            "stability": "adequate", "n": 0,
        }
    weights = [_RECENCY_DECAY ** (len(traj) - 1 - i) for i in range(len(traj))]
    nums = [to_numeric(x) for x in traj]
    total = sum(weights) or 1.0
    values = [sum(row[j] * w for row, w in zip(nums, weights)) / total for j in range(3)]
    prior_weight = 2.0 / (2.0 + len(traj))
    values = [prior_weight * prior[j] + (1 - prior_weight) * values[j] for j in range(3)]
    raw_vals = [row[0] for row in nums]
    mean = sum(raw_vals) / len(raw_vals)
    variance = sum((x - mean) ** 2 for x in raw_vals) / len(raw_vals)
    quality = max(
        {q: sum(i + 1 for i, x in enumerate(traj[-3:]) if x["quality"] == q) for q in QUALITIES},
        key=lambda q: {q2: sum(i + 1 for i, x in enumerate(traj[-3:]) if x["quality"] == q2) for q2 in QUALITIES}[q],
    )
    v, a, d = [round(max(-1.0, min(1.0, x)), 3) for x in values]
    return {
        "valence": v, "arousal": a, "dominance": d,
        "sign": _axis_label(v, "+", "0", "-"),
        "energy": _axis_label(a, "high", "normal", "low"),
        "dominance_label": _axis_label(d, "high", "normal", "low"),
        "quality": quality,
        "direction": traj[-1]["direction"],
        "stability": "rigid" if variance < 0.15 else "labile" if variance > 0.75 else "adequate",
        "n": len(traj),
    }


def mood_label(mv: dict) -> str:
    return (
        f"{mv.get('quality', 'спокойствие')} {mv.get('sign', '0')} "
        f"{mv.get('energy', 'normal')} dom:{mv.get('dominance_label', 'normal')} "
        f"{mv.get('stability', 'adequate')}"
    )


def coerce_bot_mood(value: object) -> str:
    return str(value) if value in BOT_MOODS else "раскачивание"


def _frequency_path():
    return vault.face_dir() / "mask_preferences.json"


def _empty_preferences() -> dict:
    return {
        "version": 1,
        "coefficients": {m: 0.0 for m in BOT_MOODS},
        "like_counts": {m: 0 for m in BOT_MOODS},
        "answer_count": 0,
    }


def load_mask_frequency_draft() -> dict:
    if userctx.current_uid() is None or not _frequency_path().exists():
        return _empty_preferences()
    try:
        raw = json.loads(_frequency_path().read_text(encoding="utf-8"))
    except Exception:
        log.exception("mask preferences unreadable")
        return _empty_preferences()
    base = _empty_preferences()
    for mood in BOT_MOODS:
        try:
            base["coefficients"][mood] = round(max(0.0, min(1.0, float((raw.get("coefficients") or {}).get(mood, 0.0)))), 3)
            base["like_counts"][mood] = max(0, int((raw.get("like_counts") or {}).get(mood, 0)))
        except (TypeError, ValueError):
            pass
    base.update({k: raw.get(k) for k in ("updated_at", "last_answer_at", "last_like_at", "last_bot_mood")})
    base["answer_count"] = max(0, int(raw.get("answer_count") or 0))
    return base


def load_curated_mask_frequencies() -> dict[str, float]:
    """Compatibility: внешнего curated-слоя больше нет."""
    return {m: 0.0 for m in BOT_MOODS}


def mask_like_coefficient(likes: int) -> float:
    count = max(0, int(likes))
    return round(min(0.999, count / (count + 4.0)), 3) if count else 0.0


def _save_preferences(data: dict) -> dict:
    atomic_write_json(_frequency_path(), data)
    return data


def record_mask_frequency_draft(
    llm_coefficients: object | None = None,
    *,
    bot_mood: str | None = None,
    at: object | None = None,
) -> dict:
    data = load_mask_frequency_draft()
    if isinstance(llm_coefficients, dict):
        for mood in BOT_MOODS:
            if mood in llm_coefficients:
                try:
                    data["coefficients"][mood] = round(max(0.0, min(1.0, float(llm_coefficients[mood]))), 3)
                except (TypeError, ValueError):
                    pass
    now = at.isoformat(timespec="seconds") if isinstance(at, datetime) else str(at or datetime.now().isoformat(timespec="seconds"))
    data["updated_at"] = now
    data["last_answer_at"] = now
    data["last_bot_mood"] = coerce_bot_mood(bot_mood)
    data["answer_count"] += 1
    return _save_preferences(data)


def record_mask_like(bot_mood: str | None, *, at: object | None = None) -> dict:
    data = load_mask_frequency_draft()
    mood = coerce_bot_mood(bot_mood)
    data["like_counts"][mood] += 1
    data["coefficients"][mood] = max(
        data["coefficients"][mood],
        mask_like_coefficient(data["like_counts"][mood]),
    )
    data["last_like_at"] = at.isoformat(timespec="seconds") if isinstance(at, datetime) else str(at or datetime.now().isoformat(timespec="seconds"))
    return _save_preferences(data)


def weighted_bot_mood(candidates: list[str] | tuple[str, ...]) -> str | None:
    options = [m for m in candidates if m in BOT_MOODS]
    if not options:
        return None
    frequencies = load_mask_frequency_draft()["coefficients"]
    weights = [max(0.0, float(frequencies.get(m, 0.0))) for m in options]
    return random.choices(options, weights=weights, k=1)[0] if any(weights) else random.choice(options)


def random_bot_mood() -> str:
    return weighted_bot_mood(BOT_MOODS) or "раскачивание"


def llm_error_fallback_reply() -> str:
    return random.choice(tuple(LLM_ERROR_FALLBACK_REPLIES.values()))


def opposite_bot_mood(value: object, *, exclude: set[str] | None = None) -> str | None:
    current = coerce_bot_mood(value)
    blocked = {coerce_bot_mood(x) for x in (exclude or set())}
    blocked.add(current)
    pool = _HARD + _ACTIVE if current in _SOFT else _SOFT if current in _HARD else _HARD + _SOFT
    return weighted_bot_mood([m for m in pool if m not in blocked])


def _default_faces(mv: dict) -> tuple[str, ...]:
    v = float(mv.get("valence", 0.0))
    dom = mv.get("dominance_label", "normal")
    if dom == "low" and v < 0:
        return ("вселение_уверенности", "вера", "ласка", "клятва")
    if dom == "high" and v >= 0:
        return ("сомнение", "холодная_отстранённость", "насмешка")
    if v < 0:
        return ("вселение_уверенности", "ласка", "вера")
    return ("раскачивание", "подшучивание", "сомнение")


def pick_bot_mood(mood_vec: dict) -> str:
    return weighted_bot_mood(_default_faces(mood_vec)) or "раскачивание"


def log_turn(
    mood_vec: dict,
    bot_mood: str,
    *,
    raw_event_id: str | None = None,
    session_id: str | None = None,
    q_num: int | None = None,
) -> None:
    try:
        entry = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "raw_event_id": raw_event_id,
            "session_id": session_id,
            "q_num": q_num,
            **{k: mood_vec.get(k) for k in (
                "sign", "energy", "direction", "quality", "valence",
                "arousal", "dominance", "dominance_label", "stability", "n",
            )},
            "bot_mood": coerce_bot_mood(bot_mood),
        }
        path = vault.mood_dir() / "events" / f"{datetime.now():%Y-%m}.jsonl"
        lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
        lines.append(json.dumps(entry, ensure_ascii=False))
        atomic_write_text(path, "\n".join(lines) + "\n")
    except Exception:
        log.exception("mood event write failed (non-fatal)")
