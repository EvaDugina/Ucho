"""Сравнение методов оценки настроения/состояния по сообщению (OWNER-тестирование).

Гоняет НЕСКОЛЬКО независимых методов на один ответ человека (в контексте сессии),
складывает их выводы в один отчёт (шлётся владельцу ПЕРЕД основным ответом) и пишет
их рядом в `_analysis_log.jsonl` — чтобы потом на практике выбрать самые точные.

Методы (провайдеры):
- **pad** — текущий пайплайн настроения (Qwen-классификатор + код): эмоция + V/A/D.
- **vad_lex** — нативный VAD-лексикон NRC-VAD (`bot/lexicon.py`).
- **emolex** — эмо-лексикон NRC-EmoLex, Плутчик-8 (`bot/emolex.py`).
- **dostoevsky** — тональность RuSentiment (`bot/sentiment_dvk.py`, graceful-optional).
- **ocean** / **panas** — Big Five + аффект через Qwen-промпт (`llm.analyze_psych`).

Принцип проекта сохранён: методы дают сигнал, арбитр-персона (Qwen) отвечает отдельно.
Любой сбой провайдера → None (не участвует), обработка ответа не падает.
"""
from __future__ import annotations

import asyncio
import json
import logging

from . import emolex, lexicon, llm, sentiment_dvk, userctx
from .atomic import atomic_write_text

log = logging.getLogger(__name__)

_LOG_MAX = 200  # кольцо журнала сравнения


def _log_path():
    return userctx.user_root() / "_analysis_log.jsonl"


async def run_all(
    text: str,
    history: list[dict] | None,
    *,
    mood_vec: dict | None,
    vad: dict | None,
) -> dict:
    """Запустить все методы конкурентно. `mood_vec`/`vad` уже посчитаны пайплайном
    настроения — переиспользуем, не дублируем вызовы. Возвращает {метод: результат|None}.
    """
    loop = asyncio.get_event_loop()
    emolex_fut = loop.run_in_executor(None, emolex.score_sync, text)
    dvk_fut = loop.run_in_executor(None, sentiment_dvk.score_sync, text)
    psych_fut = asyncio.ensure_future(llm.analyze_psych(text, history))

    emolex_r, dvk_r, psych_r = await asyncio.gather(
        emolex_fut, dvk_fut, psych_fut, return_exceptions=True,
    )

    def _ok(r):
        if isinstance(r, Exception):
            log.warning("analysis provider failed: %r", r)
            return None
        return r

    emolex_r = _ok(emolex_r)
    dvk_r = _ok(dvk_r)
    psych_r = _ok(psych_r)

    return {
        "pad": _pad_view(mood_vec),
        "vad_lex": vad,
        "emolex": emolex_r,
        "dostoevsky": dvk_r,
        "ocean": (psych_r or {}).get("ocean") if psych_r else None,
        "panas": (psych_r or {}).get("panas") if psych_r else None,
    }


def _pad_view(mv: dict | None) -> dict | None:
    if not isinstance(mv, dict):
        return None
    return {
        "quality": mv.get("quality"),
        "valence": mv.get("valence"), "arousal": mv.get("arousal"),
        "dominance": mv.get("dominance"), "dominance_label": mv.get("dominance_label"),
        "stability": mv.get("stability"),
    }


# --- словари расшифровки (число → понятная фраза) ---
_EMO_RU = {
    "anger": "гнев", "anticipation": "предвкушение", "disgust": "отвращение",
    "fear": "страх", "joy": "радость", "sadness": "грусть", "surprise": "удивление",
    "trust": "доверие",
}
_DVK_RU = {
    "positive": "позитив", "negative": "негатив", "neutral": "нейтрально",
    "skip": "не определить", "speech": "речевой этикет",
}
_STAB_RU = {
    "rigid": "застрял в одном состоянии", "labile": "настроение скачет",
    "adequate": "ровные колебания",
}
_DOM_LABEL_RU = {"high": "контроль/сила", "normal": "обычный контроль", "low": "бессилие/придавлен"}
# Big Five: ярлык + что значит высокий полюс.
_OCEAN_RU = (
    ("openness", "открытость", "любознательность, тяга к новому и идеям"),
    ("conscientiousness", "добросовестность", "организованность, дисциплина"),
    ("extraversion", "экстраверсия", "общительность, энергия вовне"),
    ("agreeableness", "доброжелательность", "теплота, уступчивость, эмпатия"),
    ("neuroticism", "нейротизм", "тревожность, эмоц. неустойчивость"),
)
_PANAS_RU = (
    ("positive_affect", "позитивный аффект", "бодрость, интерес, энтузиазм"),
    ("negative_affect", "негативный аффект", "тревога, раздражение, подавленность"),
)


def _axis_ru(x, kind: str) -> str:
    """Ось VAD ∈[-1..1] → понятная фраза."""
    if x is None:
        return "—"
    hi, lo = x > 0.33, x < -0.33
    if kind == "valence":
        return "позитив (хорошее)" if hi else ("негатив (плохое)" if lo else "нейтрально")
    if kind == "arousal":
        return "много энергии/возбуждён" if hi else ("вялость, мало сил" if lo else "ровная энергия")
    if kind == "dominance":
        return "чувствует силу/контроль" if hi else ("бессилие, не управляет" if lo else "обычный контроль")
    return ""


def _level01_ru(x) -> str:
    """Шкала 0..1 → низко/средне/высоко."""
    if x is None:
        return "—"
    return "высоко" if x > 0.66 else ("низко" if x < 0.34 else "средне")


def format_report(mood_vec: dict | None, bot_mood: str | None, results: dict) -> str:
    """Единое сообщение-сравнение методов для владельца (перед основным ответом).

    После каждого вычисленного числа — текстовая расшифровка (числа без пояснений
    мало о чём говорят). Только числа/ярлыки (без слов человека) → шлём напрямую,
    мимо `_send_question`/qmap (это не вопрос).
    """
    L = ["🧪 Анализ ответа — методы (число → пояснение)"]

    pad = results.get("pad")
    L.append("\n▸ PAD (Qwen+код), вектор по сессии")
    if pad:
        L.append(f"эмоция: {pad.get('quality')}")
        L.append(f"валентность: {pad.get('valence')} — {_axis_ru(pad.get('valence'), 'valence')}")
        L.append(f"энергия: {pad.get('arousal')} — {_axis_ru(pad.get('arousal'), 'arousal')}")
        L.append(f"доминирование: {pad.get('dominance')} — {_DOM_LABEL_RU.get(pad.get('dominance_label'), '—')}")
        L.append(f"устойчивость: {pad.get('stability')} — {_STAB_RU.get(pad.get('stability'), '—')}")
        L.append(f"выбранное лицо Иуды: {bot_mood or '—'}")
    else:
        L.append("нет данных")

    vad = results.get("vad_lex")
    L.append("\n▸ NRC-VAD (лексикон, по словам)")
    if vad:
        L.append(f"валентность: {vad.get('valence')} — {_axis_ru(vad.get('valence'), 'valence')}")
        L.append(f"возбуждение: {vad.get('arousal')} — {_axis_ru(vad.get('arousal'), 'arousal')}")
        L.append(f"доминирование: {vad.get('dominance')} — {_axis_ru(vad.get('dominance'), 'dominance')}")
        L.append(f"(слов из лексикона: {vad.get('n')})")
    else:
        L.append("нет совпадений со словарём")

    emo = results.get("emolex")
    L.append("\n▸ NRC-EmoLex (эмоции Плутчика, по словам)")
    if emo:
        top = ", ".join(f"{_EMO_RU.get(e, e)} {emo.get(e)}" for e in (emo.get("top") or [])) or "нет выраженных"
        L.append(f"ведущие эмоции: {top}")
        L.append(f"полярность: позитив {emo.get('positive')} / негатив {emo.get('negative')} — "
                 f"{'преобладает негатив' if (emo.get('negative') or 0) > (emo.get('positive') or 0) else ('преобладает позитив' if (emo.get('positive') or 0) > (emo.get('negative') or 0) else 'поровну')}")
        L.append(f"(слов из лексикона: {emo.get('n')})")
    else:
        L.append("нет совпадений со словарём")

    dvk = results.get("dostoevsky")
    L.append("\n▸ Dostoevsky (тональность, RuSentiment)")
    if dvk:
        L.append(f"{_DVK_RU.get(dvk.get('label'), dvk.get('label'))}: {dvk.get('score')} — уверенность модели")
    else:
        L.append("нет данных (модель не загружена)")

    oc = results.get("ocean")
    L.append("\n▸ Big Five / OCEAN (Qwen, 0..1)")
    if oc:
        for key, ru, meaning in _OCEAN_RU:
            v = oc.get(key)
            L.append(f"{ru}: {v} — {_level01_ru(v)} ({meaning})")
    else:
        L.append("нет данных")

    pa = results.get("panas")
    L.append("\n▸ PANAS (Qwen, аффект сейчас, 0..1)")
    if pa:
        for key, ru, meaning in _PANAS_RU:
            v = pa.get(key)
            L.append(f"{ru}: {v} — {_level01_ru(v)} ({meaning})")
    else:
        L.append("нет данных")

    return "\n".join(L)


def log_analysis(text_len: int, results: dict) -> None:
    """Дописать выводы всех методов в `_analysis_log.jsonl` (кольцо) для сравнения.
    Слова человека НЕ пишем — только длину сообщения и числовые выводы методов."""
    try:
        from datetime import datetime
        entry = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "len": text_len,
            **{k: results.get(k) for k in ("pad", "vad_lex", "emolex", "dostoevsky", "ocean", "panas")},
        }
        p = _log_path()
        lines = p.read_text(encoding="utf-8").splitlines() if p.exists() else []
        lines.append(json.dumps(entry, ensure_ascii=False))
        atomic_write_text(p, "\n".join(lines[-_LOG_MAX:]) + "\n")
    except Exception:
        log.exception("log_analysis failed (non-fatal)")
