"""Юнит-тесты дневного маркера (гейт «один дневной вопрос в день на пользователя»).

Маркер `last_daily_date` в `_state.json` — общий для cron и догона
после простоя. Сам send_daily_question дёргает LLM, поэтому тестируем здесь
именно гейт дедупа (vault), без сети.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from bot import session, session_log, userctx, vault
from bot.errors import LLMError
from bot.services import daily_service, reminder_service

TZ = "Europe/Moscow"


class _FakeBot:
    def __init__(self):
        self.sent: list[dict] = []

    async def send_chat_action(self, *args, **kwargs):
        return None

    async def send_message(self, chat_id, text, **kwargs):
        self.sent.append({"chat_id": chat_id, "text": text, "kwargs": kwargs})
        return SimpleNamespace(
            message_id=700 + len(self.sent),
            date=datetime(2026, 5, 26, 19, 0, tzinfo=ZoneInfo(TZ)),
        )


def _today() -> str:
    return datetime.now(ZoneInfo(TZ)).date().isoformat()


def _dt(day: str, hh: int, mm: int = 0) -> datetime:
    return datetime.combine(date.fromisoformat(day), time(hh, mm), tzinfo=ZoneInfo(TZ))


def _seed_daily_question(
    uid: int,
    *,
    day: str,
    q_num: int = 1,
    question: str = "Что сегодня не врёт?",
    sent_hour: int = 19,
):
    userctx.set_user(uid)
    vault.ensure_layout()
    s = session.start(mode="probe", domain="everyday")
    session.set_question(question, "everyday", q_num=q_num)
    event = session_log.append_required(
        session_id=s.id,
        role="assistant",
        kind="question",
        text=question,
        at=_dt(day, sent_hour),
        message_id=100 + q_num,
        q_num=q_num,
        domain="everyday",
    )
    s.add_message_id(100 + q_num)
    vault.mark_daily_sent_details(
        TZ,
        q_num=q_num,
        session_id=s.id,
        sent_at=_dt(day, sent_hour),
    )
    return s, event


def test_daily_marker_roundtrip(as_user):
    assert vault.daily_already_sent(TZ) is False
    vault.mark_daily_sent(TZ)
    assert vault.daily_already_sent(TZ) is True


def test_daily_record_keeps_question_metadata(as_user):
    day = _today()
    sent_at = _dt(day, 19)

    vault.mark_daily_sent_details(TZ, q_num=42, session_id="sid42", sent_at=sent_at)

    rec = vault.daily_record(TZ, day=day)
    assert rec["q_num"] == 42
    assert rec["session_id"] == "sid42"
    assert rec["sent_at"] == sent_at.isoformat(timespec="seconds")
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
        return {
            "question": "Что сегодня не врёт?",
            "area": "practice",
            "category": "lifestyle",
            "theme": "быт",
            "theme_key": "practice/lifestyle/быт",
        }

    monkeypatch.setattr(daily_service, "ask_next", fake_ask_next)
    monkeypatch.setattr(
        daily_service.vault,
        "commit_all",
        lambda message, allow_empty=False: commits.append(message) or "sha",
    )

    assert await daily_service.send_daily_question(_FakeBot(), as_user) is True
    assert vault.daily_already_sent(TZ) is True
    assert vault.daily_record(TZ)["q_num"] == 1
    assert commits == ["daily question"]


def test_reminder_batch_time_stays_inside_cross_midnight_window(monkeypatch):
    day = "2026-06-02"
    now = _dt(day, 23)
    monkeypatch.setattr(reminder_service.random, "randint", lambda start, end: 60)

    at = reminder_service.choose_batch_time(day, now=now)

    assert at == now + timedelta(seconds=60)
    start, end = reminder_service.reminder_window(day)
    assert start <= at < end
    assert reminder_service.reminder_day_for_now(_dt("2026-06-03", 0, 30)) == day


@pytest.mark.asyncio
async def test_reminder_plan_uses_one_batch_time_for_all_targets(as_user, monkeypatch):
    day = _today()
    uid1 = as_user
    uid2 = as_user + 10_000
    _seed_daily_question(uid1, day=day, q_num=1)
    _seed_daily_question(uid2, day=day, q_num=2)
    monkeypatch.setattr(reminder_service, "daily_targets", lambda: [uid1, uid2])
    monkeypatch.setattr(reminder_service.random, "randint", lambda start, end: 90)

    at, targets = await reminder_service.ensure_daily_reminder_plan(now=_dt(day, 23))

    assert at == _dt(day, 23) + timedelta(seconds=90)
    assert {t.uid for t in targets} == {uid1, uid2}
    plans = []
    for uid in (uid1, uid2):
        userctx.set_user(uid)
        plans.append(vault.daily_reminder_plan(TZ, day=day)["at"])
    assert len(set(plans)) == 1


@pytest.mark.asyncio
async def test_due_reminder_skips_if_user_answered_after_plan(as_user, monkeypatch):
    day = _today()
    uid = as_user
    s, _ = _seed_daily_question(uid, day=day, q_num=1)
    vault.mark_daily_reminder_planned(TZ, _dt(day, 23), day=day)
    session_log.append_required(
        session_id=s.id,
        role="user",
        kind="answer",
        text="Отвечаю поздно, но отвечаю.",
        at=_dt(day, 23, 5),
        message_id=500,
        q_num=1,
        domain="everyday",
    )
    monkeypatch.setattr(reminder_service, "daily_targets", lambda: [uid])
    monkeypatch.setattr(reminder_service.vault, "commit_all", lambda *args, **kwargs: "sha")
    bot = _FakeBot()

    result = await reminder_service.send_due_daily_reminders(bot, now=_dt(day, 23, 10))

    assert result == {"sent": 0, "skipped": 1, "errors": 0}
    assert bot.sent == []
    assert vault.daily_reminder_plan(TZ, day=day)["done"] is True


@pytest.mark.asyncio
async def test_reminder_does_not_replace_active_daily_question(as_user, monkeypatch):
    day = _today()
    uid = as_user
    s, _ = _seed_daily_question(uid, day=day, q_num=1, question="Что ты не сказал сегодня?")
    vault.mark_daily_reminder_planned(TZ, _dt(day, 23), day=day)

    async def fake_remind_presence(question: str, *, bot_mood: str):
        _ = bot_mood
        return f"Я всё ещё здесь. Жду: {question}"

    monkeypatch.setattr(reminder_service, "daily_targets", lambda: [uid])
    monkeypatch.setattr(reminder_service, "remind_presence", fake_remind_presence)
    monkeypatch.setattr(reminder_service.moods, "random_bot_mood", lambda: "вера")
    monkeypatch.setattr(reminder_service.vault, "commit_all", lambda *args, **kwargs: "sha")
    bot = _FakeBot()

    result = await reminder_service.send_due_daily_reminders(bot, now=_dt(day, 23, 10))

    assert result == {"sent": 1, "skipped": 0, "errors": 0}
    assert session.get().id == s.id
    assert session.get().last_question == "Что ты не сказал сегодня?"
    events = session_log.session_events(s.id)
    assert events[-1]["kind"] == "reminder"
    assert events[-1]["q_num"] == 1
    assert session_log.find_question_by_q_num(1)["text"] == "Что ты не сказал сегодня?"


@pytest.mark.asyncio
async def test_reminder_uses_short_fallback_when_llm_unavailable(as_user, monkeypatch):
    day = _today()
    uid = as_user
    s, _ = _seed_daily_question(uid, day=day, q_num=1, question="Что ты не сказал сегодня?")
    vault.mark_daily_reminder_planned(TZ, _dt(day, 23), day=day)

    async def fail_remind_presence(question: str, *, bot_mood: str):
        _ = question, bot_mood
        raise LLMError("down")

    monkeypatch.setattr(reminder_service, "daily_targets", lambda: [uid])
    monkeypatch.setattr(reminder_service, "remind_presence", fail_remind_presence)
    monkeypatch.setattr(reminder_service.moods, "random_bot_mood", lambda: "вера")
    monkeypatch.setattr(reminder_service.vault, "commit_all", lambda *args, **kwargs: "sha")
    bot = _FakeBot()

    result = await reminder_service.send_due_daily_reminders(bot, now=_dt(day, 23, 10))

    assert result == {"sent": 1, "skipped": 0, "errors": 0}
    assert bot.sent[0]["text"].startswith("Я всё ещё здесь")
    events = session_log.session_events(s.id)
    assert events[-1]["kind"] == "reminder"
    assert events[-1]["text"] == "Я всё ещё здесь"
