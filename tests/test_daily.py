"""Юнит-тесты дневного маркера (гейт «один дневной вопрос в день на пользователя»).

Маркер `last_daily_date` в `_state.json` — общий для cron, /dailyall и догона
после простоя. Сам send_daily_question дёргает LLM, поэтому тестируем здесь
именно гейт дедупа (vault), без сети.
"""
from __future__ import annotations

from bot import vault

TZ = "Europe/Moscow"


def test_daily_marker_roundtrip(as_user):
    assert vault.daily_already_sent(TZ) is False
    vault.mark_daily_sent(TZ)
    assert vault.daily_already_sent(TZ) is True
    # Идемпотентность: повторная отметка в тот же день — всё ещё «отправлен».
    vault.mark_daily_sent(TZ)
    assert vault.daily_already_sent(TZ) is True


def test_daily_marker_resets_on_new_day(as_user):
    vault.mark_daily_sent(TZ)
    assert vault.daily_already_sent(TZ) is True
    # Сымитируем, что последняя отправка была в прошлом → сегодня снова можно
    # (нет бэкфилла: догон шлёт только сегодняшний, прошлые дни не досылаются).
    state = vault._load_state()
    state["last_daily_date"] = "2000-01-01"
    vault._save_state(state)
    assert vault.daily_already_sent(TZ) is False
