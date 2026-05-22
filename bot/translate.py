"""RU→EN перевод (локально, офлайн) через Argos Translate — для VADER-сигнала.

Argos работает на CPU, в процессе бота, без сети (модель ru→en вшита в образ на
build-стадии, см. Dockerfile). Используется ТОЛЬКО для инструментальной оценки
тональности VADER; сам перевод нигде не хранится. Любой сбой → '' (VADER тогда
пропускается, обработка ответа не падает).
"""
import asyncio
import logging
from functools import lru_cache

log = logging.getLogger(__name__)

_ready: bool | None = None  # None — ещё не пробовали; True/False — итог инициализации


def _ensure() -> bool:
    """Лениво проверить, что argostranslate доступен (модель ru→en вшита)."""
    global _ready
    if _ready is not None:
        return _ready
    try:
        import argostranslate.translate  # noqa: F401
        _ready = True
    except Exception:
        log.exception("argostranslate недоступен — перевод для VADER отключён")
        _ready = False
    return _ready


@lru_cache(maxsize=512)
def _translate_sync(text: str) -> str:
    try:
        import argostranslate.translate as t
        return (t.translate(text, "ru", "en") or "").strip()
    except Exception:
        log.exception("argos translate failed (non-fatal)")
        return ""


async def translate_ru_en(text: str) -> str:
    """RU→EN или '' при пустом вводе/сбое. CPU-перевод гоним в executor, чтобы
    не блокировать event loop бота."""
    text = (text or "").strip()
    if not text or not _ensure():
        return ""
    try:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _translate_sync, text)
    except Exception:
        log.exception("translate_ru_en failed (non-fatal)")
        return ""
