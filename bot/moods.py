"""Настроение пользователя и «лица» Иуды.

Разделение труда (как и везде в проекте):
- **LLM** только классифицирует последнее сообщение категориально
  (`sign/energy/direction/quality/dominance`) — `llm.classify_mood`.
- **Код (этот модуль)** считает математику: вектор настроения по сессии с
  приоритетом на последнее сообщение (recency) + затухающий prior из портрета,
  устойчивость (дисперсия), выбор контрастного лица, журнал и mood-map.
- **depersonalization (сильная модель)** строит граф `mood/` и пишет `_mood_map.json`
  + `mood_baseline` в портрет.

Часть 1 (Фаза B) — измерения + `session_mood`. Часть 2 (Фаза C) — `BOT_MOODS`,
`DEFAULT_CONTRARIAN`, `pick_bot_mood`, `log_turn`, `load_mood_map`.
"""
from __future__ import annotations

import json
import logging
import random

from . import userctx
from .atomic import atomic_write_text

log = logging.getLogger(__name__)

# --- закрытые словари измерений (вход от LLM-классификатора) ---
SIGNS = ("+", "0", "-")
ENERGY = ("high", "normal", "low")
DIRECTION = ("auto", "hetero", "neutral")
# Доминирование (ось D из PAD): чувствует ли человек контроль/силу в ситуации.
# high — владеет положением/доминирует, low — придавлен/бессилен, normal — норма.
DOMINANCE = ("high", "normal", "low")
# Фоновая эмоция (quality) — закрытый список ~12.
QUALITIES = (
    "тревога", "страх", "грусть_тоска", "апатия_подавленность",
    "раздражение_гнев", "стыд_вина", "спокойствие", "сосредоточенность",
    "радость", "воодушевление_азарт", "гордость_самоуверенность",
    "презрение_зависть",
)

# --- параметры математики (подбираются на e2e) ---
_RECENCY_DECAY = 0.6   # вес сообщения = decay^(расстояние от последнего); последнее = 1
_PRIOR_BASE = 2.0      # вес prior = base/(base+n): n=0 → весь prior, дальше затухает
_RIGID_VAR = 0.15      # дисперсия valence < этого → ригидное (застрял)
_LABILE_VAR = 0.75     # дисперсия valence > этого → лабильное (скачет)


def coerce_sign(s) -> str:
    return s if s in SIGNS else "0"


def coerce_energy(s) -> str:
    return s if s in ENERGY else "normal"


def coerce_direction(s) -> str:
    return s if s in DIRECTION else "neutral"


def coerce_quality(s) -> str:
    return s if s in QUALITIES else "спокойствие"


def coerce_dominance(s) -> str:
    return s if s in DOMINANCE else "normal"


def normalize_per_msg(per_msg: dict) -> dict:
    """Привести вектор одного сообщения к валидным категориям (whitelist+фолбэк)."""
    d = per_msg if isinstance(per_msg, dict) else {}
    return {
        "sign": coerce_sign(d.get("sign")),
        "energy": coerce_energy(d.get("energy")),
        "direction": coerce_direction(d.get("direction")),
        "quality": coerce_quality(d.get("quality")),
        "dominance": coerce_dominance(d.get("dominance")),
    }


def _sign_num(s: str) -> int:
    return {"+": 1, "0": 0, "-": -1}.get(s, 0)


def _energy_num(e: str) -> int:
    return {"high": 1, "normal": 0, "low": -1}.get(e, 0)


def _dominance_num(d: str) -> int:
    return {"high": 1, "normal": 0, "low": -1}.get(d, 0)


def to_numeric(per_msg: dict) -> tuple[int, int, int]:
    """Категории сообщения → (valence, arousal, dominance) ∈ {-1,0,1}."""
    p = normalize_per_msg(per_msg)
    return _sign_num(p["sign"]), _energy_num(p["energy"]), _dominance_num(p["dominance"])


def _sign_label(x: float) -> str:
    return "+" if x > 0.33 else ("-" if x < -0.33 else "0")


def _energy_label(x: float) -> str:
    return "high" if x > 0.33 else ("low" if x < -0.33 else "normal")


def _dominance_label(x: float) -> str:
    return "high" if x > 0.33 else ("low" if x < -0.33 else "normal")


def _dominant_quality(traj: list[dict]) -> str:
    """Доминирующая фоновая эмоция из последних сообщений (приоритет — последним)."""
    if not traj:
        return "спокойствие"
    recent = traj[-3:]
    counts: dict[str, int] = {}
    for i, p in enumerate(recent):
        q = coerce_quality((p or {}).get("quality"))
        counts[q] = counts.get(q, 0) + (i + 1)  # позже = больше вес
    return max(counts, key=counts.get)


def session_mood(
    trajectory: list[dict], prior: tuple[float, float, float] = (0.0, 0.0, 0.0)
) -> dict:
    """Вектор настроения по сессии: recency-взвешенное среднее + затухающий prior.

    trajectory — список per-message векторов (см. classify_mood). prior —
    (valence, arousal, dominance) из портрета (`about.mood_baseline`); доминирует
    на старте сессии, затухает по мере накопления сообщений. Допускается
    2-элементный prior (старый формат) — dominance тогда 0.0.

    Returns: {valence, arousal, dominance ∈[-1..1]; sign, energy, dominance_label —
    дискретно; quality; direction; stability ∈ {labile,adequate,rigid}; n}.
    """
    traj = [t for t in (trajectory or []) if isinstance(t, dict)]
    n = len(traj)
    pv = float(prior[0])
    pa = float(prior[1])
    pd = float(prior[2]) if len(prior) > 2 else 0.0
    if n == 0:
        return {
            "valence": pv, "arousal": pa, "dominance": pd,
            "sign": _sign_label(pv), "energy": _energy_label(pa),
            "dominance_label": _dominance_label(pd),
            "quality": "спокойствие", "direction": "neutral",
            "stability": "adequate", "n": 0,
        }

    # recency-веса: последнее сообщение — наибольший вес (decay^0 = 1).
    vals, ars, doms, weights = [], [], [], []
    for i, p in enumerate(traj):
        v, a, d = to_numeric(p)
        w = _RECENCY_DECAY ** (n - 1 - i)
        vals.append(v)
        ars.append(a)
        doms.append(d)
        weights.append(w)
    wsum = sum(weights) or 1.0
    v_rec = sum(v * w for v, w in zip(vals, weights)) / wsum
    a_rec = sum(a * w for a, w in zip(ars, weights)) / wsum
    d_rec = sum(d * w for d, w in zip(doms, weights)) / wsum

    # затухающий blend с prior
    w_prior = _PRIOR_BASE / (_PRIOR_BASE + n)
    valence = w_prior * pv + (1 - w_prior) * v_rec
    arousal = w_prior * pa + (1 - w_prior) * a_rec
    dominance = w_prior * pd + (1 - w_prior) * d_rec

    # устойчивость — дисперсия valence по сообщениям
    mean_v = sum(vals) / n
    var_v = sum((v - mean_v) ** 2 for v in vals) / n
    stability = "rigid" if var_v < _RIGID_VAR else ("labile" if var_v > _LABILE_VAR else "adequate")

    last = normalize_per_msg(traj[-1])
    return {
        "valence": round(max(-1.0, min(1.0, valence)), 3),
        "arousal": round(max(-1.0, min(1.0, arousal)), 3),
        "dominance": round(max(-1.0, min(1.0, dominance)), 3),
        "sign": _sign_label(valence),
        "energy": _energy_label(arousal),
        "dominance_label": _dominance_label(dominance),
        "quality": _dominant_quality(traj),
        "direction": last["direction"],
        "stability": stability,
        "n": n,
    }


def mood_label(mv: dict) -> str:
    """Компактная строка настроения для портрета: «грусть_тоска − low dom:low rigid».

    `dom:` — ось доминирования (контроль↔бессилие), помечена явно, чтобы не
    путать с energy (обе используют high/normal/low).
    """
    return (
        f"{mv.get('quality','спокойствие')} {mv.get('sign','0')} "
        f"{mv.get('energy','normal')} dom:{mv.get('dominance_label','normal')} "
        f"{mv.get('stability','adequate')}"
    )


def map_key(mv: dict) -> str:
    """Ключ mood-map / узел графа: «грусть_тоска(−)»."""
    return f"{mv.get('quality','спокойствие')}({mv.get('sign','0')})"


# ===== Часть 2 (Фаза C): выбор лица, журнал, mood-map =====

# Лица Иуды (закрытый список). Режущие + тёплые.
BOT_MOODS = (
    "раскачивание", "насмешка", "подшучивание", "давление_на_больное",
    "унижение", "перевирание", "сомнение", "холодная_отстранённость",
    "ласка", "любовь", "вера", "вселение_уверенности", "смирение", "клятва",
)

_MOOD_LOG_MAX = 200  # кольцо журнала пар (настроение → лицо)
_BRIGHT_Q = {"гордость_самоуверенность", "воодушевление_азарт", "презрение_зависть"}


def coerce_bot_mood(s) -> str:
    return s if s in BOT_MOODS else "раскачивание"


def _mood_dir() -> "object":
    return userctx.user_root() / "mood"


def _mood_log_path():
    return userctx.user_root() / "_mood_log.jsonl"


def _default_faces(mv: dict) -> list[str]:
    """Контрарная политика-фолбэк (пока mood-map пуст): «в противоположность».

    Ось dominance берёт верх на полюсах (формализует политику из prompts/mood.md:
    «высокомерен — осеки; придавлен — поддержи») — её две ветки идут ПЕРВЫМИ.
    """
    v = float(mv.get("valence", 0.0))
    energy = mv.get("energy", "normal")
    q = mv.get("quality", "спокойствие")
    direction = mv.get("direction", "neutral")
    dom = mv.get("dominance_label", "normal")
    if dom == "low" and v < 0:                            # придавлен / бессилен → поддержать
        return ["вселение_уверенности", "вера", "ласка", "клятва"]
    if dom == "high" and v >= 0:                          # властен / высокомерен → осадить
        return ["сомнение", "холодная_отстранённость", "насмешка", "давление_на_больное"]
    if energy == "low" and v >= 0:                       # вял / ровен / спокоен
        return ["раскачивание", "насмешка", "подшучивание"]
    if energy == "high" and v > 0 and q in _BRIGHT_Q:     # ярок / самоуверен
        return ["насмешка", "давление_на_больное", "сомнение", "холодная_отстранённость"]
    if energy == "high" and v < 0:                        # гнев / раздражение
        return ["холодная_отстранённость", "смирение", "подшучивание"]
    if v < 0 and direction == "auto":                     # загнал себя
        return ["вселение_уверенности", "смирение", "ласка"]
    if v < 0:                                             # грусть / тревога / стыд
        return ["вселение_уверенности", "ласка", "вера", "клятва"]
    return ["раскачивание", "подшучивание", "сомнение"]   # дефолт — лёгкая провокация


def load_mood_map() -> dict:
    """Per-user рантайм-карта `mood/_mood_map.json` (пишет depersonalization). Нет → {}."""
    try:
        p = _mood_dir() / "_mood_map.json"
        if not p.exists():
            return {}
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        log.exception("load_mood_map failed (non-fatal)")
        return {}


def pick_bot_mood(mood_vec: dict) -> str:
    """Выбрать лицо Иуды контрастно настроению. Приоритет — per-user mood-map
    (ключ `quality(знак)` или `quality`); иначе контрарный фолбэк. Лёгкий рандом."""
    mp = load_mood_map()
    entry = mp.get(map_key(mood_vec)) or mp.get(mood_vec.get("quality", ""))
    candidates: list[str] = []
    if isinstance(entry, dict):
        avoid = set(entry.get("avoid") or [])
        candidates = [m for m in (entry.get("prefer") or []) if m in BOT_MOODS and m not in avoid]
    if not candidates:
        candidates = _default_faces(mood_vec)
    return coerce_bot_mood(random.choice(candidates) if candidates else "раскачивание")


def log_turn(mood_vec: dict, bot_mood: str, vad: dict | None = None) -> None:
    """Записать пару (настроение → лицо) в `_mood_log.jsonl` (кольцо). Для графа
    Фазы D (depersonalization агрегирует в `mood/` + `_mood_map.json`). `vad` —
    нативная русская VAD-оценка лексикона (`lexicon.score`): сверка арбитр↔лексикон
    (где LLM перебивает детерминированный сигнал)."""
    try:
        from datetime import datetime
        vad = vad if isinstance(vad, dict) else {}
        entry = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "sign": mood_vec.get("sign"), "energy": mood_vec.get("energy"),
            "direction": mood_vec.get("direction"), "quality": mood_vec.get("quality"),
            "valence": mood_vec.get("valence"), "arousal": mood_vec.get("arousal"),
            "dominance": mood_vec.get("dominance"),
            "stability": mood_vec.get("stability"), "bot_mood": coerce_bot_mood(bot_mood),
            "lex_valence": vad.get("valence"),
            "lex_arousal": vad.get("arousal"),
            "lex_dominance": vad.get("dominance"),
        }
        p = _mood_log_path()
        lines = p.read_text(encoding="utf-8").splitlines() if p.exists() else []
        lines.append(json.dumps(entry, ensure_ascii=False))
        atomic_write_text(p, "\n".join(lines[-_MOOD_LOG_MAX:]) + "\n")
    except Exception:
        log.exception("log_turn failed (non-fatal)")
