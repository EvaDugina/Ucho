"""Юнит-тесты дневного маркера (гейт «один дневной вопрос в день на пользователя»).

Маркер `last_daily_date` в `_state.json` — общий для cron, /dailyall и догона
после простоя. Сам send_daily_question дёргает LLM, поэтому тестируем здесь
именно гейт дедупа (vault), без сети.
"""
from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import pytest

from bot import vault
from bot.services import daily_service

TZ = "Europe/Moscow"


class _FakeBot:
    async def send_chat_action(self, *args, **kwargs):
        return None

    async def send_message(self, chat_id, text, **kwargs):
        _ = kwargs
        return SimpleNamespace(message_id=700, date=datetime(2026, 5, 26, 19, 0))


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


@pytest.mark.asyncio
async def test_send_daily_question_commits_after_marker(as_user, monkeypatch):
    commits: list[str] = []

    async def fake_ask_next(**kwargs):
        _ = kwargs
        return {"question": "Что сегодня не врёт?", "domain": "everyday"}

    monkeypatch.setattr(daily_service, "ask_next", fake_ask_next)
    monkeypatch.setattr(daily_service.random, "choice", lambda seq: "everyday")
    monkeypatch.setattr(
        daily_service.vault,
        "commit_all",
        lambda message, allow_empty=False: commits.append(message) or "sha",
    )

    assert await daily_service.send_daily_question(_FakeBot(), as_user) is True
    assert vault.daily_already_sent(TZ) is True
    assert commits == ["daily question"]
