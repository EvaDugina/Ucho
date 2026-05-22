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


def _fmt_kv(d: dict, keys, prec=2) -> str:
    return " ".join(f"{k}={round(d[k], prec)}" for k in keys if d.get(k) is not None)


def format_report(mood_vec: dict | None, bot_mood: str | None, results: dict) -> str:
    """Единое сообщение-сравнение методов для владельца (перед основным ответом).

    Только числа/ярлыки (без слов человека) → можно слать как есть. Это НЕ вопрос:
    шлётся напрямую, мимо `_send_question`/qmap.
    """
    lines = ["🧪 Анализ ответа — сравнение методов\n"]

    pad = results.get("pad")
    if pad:
        lines.append(
            f"PAD (Qwen+код): {pad.get('quality')} · "
            f"V {pad.get('valence')} A {pad.get('arousal')} D {pad.get('dominance')} "
            f"({pad.get('dominance_label')}) · {pad.get('stability')} → лицо {bot_mood or '—'}"
        )
    else:
        lines.append("PAD (Qwen+код): нет данных")

    vad = results.get("vad_lex")
    lines.append(
        f"NRC-VAD (лексикон): v={vad.get('valence')} a={vad.get('arousal')} "
        f"d={vad.get('dominance')} (слов {vad.get('n')})" if vad
        else "NRC-VAD (лексикон): нет совпадений"
    )

    emo = results.get("emolex")
    if emo:
        top = ", ".join(f"{e} {emo.get(e)}" for e in (emo.get("top") or [])) or "нейтрально"
        lines.append(f"NRC-EmoLex (Плутчик): {top} · pos {emo.get('positive')} / neg {emo.get('negative')} (слов {emo.get('n')})")
    else:
        lines.append("NRC-EmoLex (Плутчик): нет совпадений")

    dvk = results.get("dostoevsky")
    lines.append(
        f"Dostoevsky (тональность): {dvk.get('label')} {dvk.get('score')}" if dvk
        else "Dostoevsky (тональность): нет данных"
    )

    oc = results.get("ocean")
    lines.append(
        "Big Five (Qwen): " + _fmt_kv(oc, ("openness", "conscientiousness", "extraversion", "agreeableness", "neuroticism"))
        if oc else "Big Five (Qwen): нет данных"
    )

    pa = results.get("panas")
    lines.append(
        f"PANAS (Qwen): PA {pa.get('positive_affect')} / NA {pa.get('negative_affect')}" if pa
        else "PANAS (Qwen): нет данных"
    )

    return "\n".join(lines)


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
