"""Тональность по русскому тексту через Dostoevsky (FastText на RuSentiment).

5 классов: positive / negative / neutral / skip / speech. Это один из методов
сравнения (OWNER-тестирование). CPU, офлайн (модель вшивается в образ на build).

Graceful-optional: библиотека/модель могут отсутствовать (модель тянется отдельным
шагом и может не докачаться) — тогда `score`/`score_sync` отдают None и провайдер
просто не участвует в отчёте. Любой сбой → None (обработка ответа не падает).
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)

_model = None          # ленивый singleton FastTextSocialNetworkModel
_unavailable = False   # один раз пометили «нет модели/библиотеки» — больше не пробуем
_LABELS = ("positive", "negative", "neutral", "skip", "speech")


def _get_model():
    global _model, _unavailable
    if _model is not None or _unavailable:
        return _model
    try:
        from dostoevsky.models import FastTextSocialNetworkModel
        from dostoevsky.tokenization import RegexTokenizer
        _model = FastTextSocialNetworkModel(tokenizer=RegexTokenizer())
    except Exception:
        log.warning("Dostoevsky недоступен (нет библиотеки/модели) — провайдер отключён")
        _unavailable = True
    return _model


def score_sync(text: str) -> dict | None:
    """{positive,negative,neutral,skip,speech ∈[0..1], label, score} или None."""
    text = (text or "").strip()
    if not text:
        return None
    m = _get_model()
    if m is None:
        return None
    try:
        res = m.predict([text], k=len(_LABELS))[0]  # dict label→prob
    except Exception:
        log.exception("dostoevsky predict failed (non-fatal)")
        return None
    scores = {k: round(float(res.get(k, 0.0)), 3) for k in _LABELS}
    label = max(scores, key=scores.get)
    return {**scores, "label": label, "score": scores[label]}
