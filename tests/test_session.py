"""Юнит-тесты сессии: per-uid лок сериализует конкурентные мутации.

Ответы в probe не проходят single-flight ratelimit, поэтому два быстрых
сообщения одного пользователя могли бы переплестись на await-границах. Лок по
uid (session.lock_for) сериализует критическую секцию — порядок не рвётся и
история не теряется.
"""
from __future__ import annotations

import asyncio

import pytest

from bot import session, userctx


@pytest.mark.asyncio
async def test_lock_for_serializes_session_mutations(as_user):
    uid = userctx.current_uid()
    session.start(mode="probe")
    order: list[str] = []

    async def worker(tag: str) -> None:
        async with session.lock_for(uid):
            order.append(f"{tag}-start")
            await asyncio.sleep(0.01)  # окно, в которое без лока влез бы другой
            session.get().record_user(f"msg-{tag}")
            order.append(f"{tag}-end")

    await asyncio.gather(worker("a"), worker("b"))

    # Лок сериализует: одна корутина полностью завершается до старта другой.
    assert order in (
        ["a-start", "a-end", "b-start", "b-end"],
        ["b-start", "b-end", "a-start", "a-end"],
    )
    # Обе реплики записаны — потери истории нет.
    contents = [h["content"] for h in session.get().history]
    assert "msg-a" in contents and "msg-b" in contents


def test_lock_for_is_stable_per_uid(as_user):
    uid = userctx.current_uid()
    assert session.lock_for(uid) is session.lock_for(uid)
    assert session.lock_for(uid) is not session.lock_for(uid + 1)
