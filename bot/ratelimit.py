"""Per-user ограничитель LLM-операций (anti-DoS на общий GPU).

Контекст: одна локальная Ollama на одной видеокарте обслуживает всех
пользователей; запросы к ней сериализуются. Без ограничителя один гость спамом
вопросов мог бы непрерывно занимать GPU и блокировать остальных (в т.ч.
владельца). Здесь — два барьера на пользователя:

1. **single-flight** — не более одного активного LLM-вызова на пользователя.
   Пока предыдущий обрабатывается, новый отклоняется (а не ставится в очередь).
2. **cooldown** — минимальный интервал между операциями одного пользователя
   (с момента завершения предыдущей), чтобы отсечь скриптовый перебор.

Состояние — in-memory (процесс бота один). aiogram-хэндлеры исполняются в одном
event-loop, поэтому проверка/правка ``_inflight`` между ``await`` атомарна —
блокировки не нужны.

Использование в хэндлере (вокруг всего LLM-несущего тела)::

    uid = userctx.current_uid()
    if not ratelimit.try_acquire(uid):
        await message.answer(ratelimit.BUSY_MESSAGE)
        return
    try:
        ...  # LLM-вызовы
    finally:
        ratelimit.release(uid)

``release`` вызывать ТОЛЬКО если ``try_acquire`` вернул True.
"""
from __future__ import annotations

import time
from typing import Optional

from .config import LLM_COOLDOWN_SEC

BUSY_MESSAGE = (
    "Подожди — я ещё думаю над предыдущим (или совсем недавно отвечал). "
    "Дай мне пару секунд и повтори."
)

# uid с активным LLM-вызовом прямо сейчас.
_inflight: set[int] = set()
# uid → monotonic-время завершения последней операции (для cooldown).
_last_done: dict[int, float] = {}


def try_acquire(uid: Optional[int]) -> bool:
    """Попытаться занять слот пользователя под LLM-операцию.

    True — слот занят, можно работать (обязателен последующий ``release``).
    False — уже идёт операция этого пользователя ИЛИ не вышел cooldown.

    ``uid is None`` (офлайн/вне контекста: миграции, recovery, тикер) не
    ограничивается — возвращает True и НЕ заносит состояние.
    """
    if uid is None:
        return True
    if uid in _inflight:
        return False
    last = _last_done.get(uid)
    if last is not None and (time.monotonic() - last) < LLM_COOLDOWN_SEC:
        return False
    _inflight.add(uid)
    return True


def release(uid: Optional[int]) -> None:
    """Освободить слот пользователя и отметить время завершения (старт cooldown)."""
    if uid is None:
        return
    _inflight.discard(uid)
    _last_done[uid] = time.monotonic()
