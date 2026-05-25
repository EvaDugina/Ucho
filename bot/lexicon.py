"""Нативный русский VAD-сигнал по тексту (детерминированный, офлайн, CPU).

Заменяет связку `translate.translate_ru_en` → VADER: вместо перевода на английский
считаем valence/arousal/dominance прямо по-русски из лексикона NRC-VAD (русская
ветка авто-перевода). Это ИНСТРУМЕНТАЛЬНАЯ ПОДСКАЗКА арбитру-LLM (`llm.classify_mood`),
а не приговор — лексикон слеп к иронии/контексту/отрицанию.

Разделение труда не меняется: лексикон даёт сырой VAD-якорь, арбитр-LLM решает.

Угрозы/инварианты:
- Лексикон вшит в образ (`bot/data/nrc_vad_ru.tsv`), в сеть рантайм не ходит.
- Файла нет / pymorphy3 недоступен / нет совпадений слов → `score` отдаёт None
  (мягкая деградация: пайплайн настроения работает на LLM-классификаторе, ответ не падает).
- Слова человека никуда не сохраняются — только эфемерный расчёт вектора.
"""
from __future__ import annotations

import asyncio
import logging
import re
from functools import lru_cache
from pathlib import Path

log = logging.getLogger(__name__)

# Лексикон: word<TAB>valence<TAB>arousal<TAB>dominance, значения ∈ [0..1].
_LEXICON_PATH = Path(__file__).parent / "data" / "nrc_vad_ru.tsv"
_TOKEN_RE = re.compile(r"[а-яёa-z]+", re.IGNORECASE)

_lexicon: dict[str, tuple[float, float, float]] | None = None
_morph = None  # ленивый singleton pymorphy3.MorphAnalyzer
_morph_failed = False


def _load_lexicon() -> dict[str, tuple[float, float, float]]:
    """Лениво загрузить лексикон в память (один раз). Нет файла/мусор → {}."""
    global _lexicon
    if _lexicon is not None:
        return _lexicon
    data: dict[str, tuple[float, float, float]] = {}
    try:
        if _LEXICON_PATH.exists():
            for line in _LEXICON_PATH.read_text(encoding="utf-8").splitlines():
                parts = line.split("\t")
                if len(parts) < 4:
                    continue
                word = parts[0].strip().lower()
                if not word:
                    continue
                try:
                    v, a, d = float(parts[1]), float(parts[2]), float(parts[3])
                except ValueError:
                    continue  # шапка/битая строка
                data[word] = (v, a, d)
        else:
            log.warning("lexicon file not found: %s — VAD-сигнал отключён", _LEXICON_PATH)
    except Exception:
        log.exception("lexicon load failed (non-fatal)")
        data = {}
    _lexicon = data
    return _lexicon


def _get_morph():
    """Ленивый singleton pymorphy3 (для лемматизации). Сбой → None (один раз)."""
    global _morph, _morph_failed
    if _morph is not None or _morph_failed:
        return _morph
    try:
        import pymorphy3
        _morph = pymorphy3.MorphAnalyzer()
    except Exception:
        log.exception("pymorphy3 недоступен — лемматизация лексикона отключена")
        _morph_failed = True
    return _morph


@lru_cache(maxsize=4096)
def _lemma(token: str) -> str:
    m = _get_morph()
    if m is None:
        return token
    try:
        return m.parse(token)[0].normal_form
    except Exception:
        return token


def _rescale(x: float) -> float:
    """[0..1] (NRC-VAD) → [-1..1] (контракт moods)."""
    return max(-1.0, min(1.0, 2.0 * x - 1.0))


def score_sync(text: str) -> dict | None:
    """VAD-вектор текста ∈ [-1..1] или None (нет файла/совпадений/сбой).

    Усредняем V/A/D по словам текста, найденным в лексиконе (после лемматизации),
    и решейлим [0..1]→[-1..1]. Чем больше совпавших слов — тем устойчивее оценка.
    """
    text = (text or "").strip()
    if not text:
        return None
    lex = _load_lexicon()
    if not lex:
        return None
    vs, as_, ds = [], [], []
    for tok in _TOKEN_RE.findall(text.lower()):
        hit = lex.get(tok) or lex.get(_lemma(tok))
        if hit:
            vs.append(hit[0])
            as_.append(hit[1])
            ds.append(hit[2])
    if not vs:
        return None
    n = len(vs)
    return {
        "valence": round(_rescale(sum(vs) / n), 3),
        "arousal": round(_rescale(sum(as_) / n), 3),
        "dominance": round(_rescale(sum(ds) / n), 3),
        "n": n,
    }


async def score(text: str) -> dict | None:
    """Async-обёртка: CPU-расчёт (лемматизация) гоним в executor, не блокируя loop."""
    try:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, score_sync, text)
    except Exception:
        log.exception("lexicon.score failed (non-fatal)")
        return None
