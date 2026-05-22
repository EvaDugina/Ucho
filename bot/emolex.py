"""Эмоции по русскому тексту через лексикон NRC-EmoLex (Плутчик-8 + pos/neg).

Детерминированный, офлайн, CPU — как `bot/lexicon.py` (VAD), но даёт ДИСКРЕТНЫЕ
эмоции (anger/anticipation/disgust/fear/joy/sadness/surprise/trust) и полярность.
Это один из методов сравнения (OWNER-тестирование), а не приговор: слеп к иронии/
контексту/отрицанию.

Угрозы/инварианты — как у lexicon.py: лексикон вшит (`bot/data/nrc_emolex_ru.tsv`),
в сеть рантайм не ходит; нет файла/совпадений/сбой → None; слова не сохраняются.
Лемматизацию переиспользуем из `lexicon` (общий singleton pymorphy3 + кэш).
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

from .lexicon import _lemma  # общий лемматизатор (pymorphy3 singleton + LRU)

log = logging.getLogger(__name__)

_PATH = Path(__file__).parent / "data" / "nrc_emolex_ru.tsv"
_TOKEN_RE = re.compile(r"[а-яёa-z]+", re.IGNORECASE)
# Порядок колонок в tsv (см. scripts/build_emolex.py).
EMOTIONS = ("anger", "anticipation", "disgust", "fear", "joy", "sadness", "surprise", "trust")
_COLS = (*EMOTIONS, "positive", "negative")

_lexicon: dict[str, tuple[int, ...]] | None = None


def _load() -> dict[str, tuple[int, ...]]:
    global _lexicon
    if _lexicon is not None:
        return _lexicon
    data: dict[str, tuple[int, ...]] = {}
    try:
        if _PATH.exists():
            for line in _PATH.read_text(encoding="utf-8").splitlines():
                parts = line.split("\t")
                if len(parts) < 1 + len(_COLS):
                    continue
                word = parts[0].strip().lower()
                if not word:
                    continue
                try:
                    flags = tuple(int(x) for x in parts[1:1 + len(_COLS)])
                except ValueError:
                    continue
                data[word] = flags
        else:
            log.warning("emolex file not found: %s — эмо-лексикон отключён", _PATH)
    except Exception:
        log.exception("emolex load failed (non-fatal)")
        data = {}
    _lexicon = data
    return _lexicon


def score_sync(text: str) -> dict | None:
    """Доли эмоций/полярности по словам текста или None (нет файла/совпадений/сбой).

    Для каждого слова из лексикона берём его бинарные метки и усредняем по числу
    СОВПАВШИХ слов → доля ∈ [0..1] на категорию. Возвращаем все категории + `top`
    (доминирующие эмоции) + `n` (сколько слов совпало).
    """
    text = (text or "").strip()
    if not text:
        return None
    lex = _load()
    if not lex:
        return None
    sums = [0] * len(_COLS)
    n = 0
    for tok in _TOKEN_RE.findall(text.lower()):
        hit = lex.get(tok) or lex.get(_lemma(tok))
        if hit:
            n += 1
            for i, v in enumerate(hit):
                sums[i] += v
    if n == 0:
        return None
    fracs = {name: round(s / n, 3) for name, s in zip(_COLS, sums)}
    # Доминирующие эмоции (только из 8 Плутчика, доля > 0), по убыванию.
    top = sorted(
        ((e, fracs[e]) for e in EMOTIONS if fracs[e] > 0),
        key=lambda kv: kv[1], reverse=True,
    )
    return {**fracs, "top": [e for e, _ in top[:3]], "n": n}
