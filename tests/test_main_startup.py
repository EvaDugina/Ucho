from __future__ import annotations

from types import SimpleNamespace

import pytest

from bot import main as main_mod


class _FakeBot:
    def __init__(self, *args, **kwargs):
        self.session = SimpleNamespace(close=self._close)
        self.closed = False

    async def _close(self):
        self.closed = True


class _MiddlewareTarget:
    def middleware(self, *args, **kwargs):
        return None


class _FakeDispatcher:
    def __init__(self):
        self.message = _MiddlewareTarget()
        self.callback_query = _MiddlewareTarget()
        self.started = False

    def include_router(self, *args, **kwargs):
        return None

    def errors(self):
        def decorator(func):
            return func

        return decorator

    async def start_polling(self, bot):
        self.started = True


@pytest.mark.asyncio
async def test_dev_startup_skips_background_jobs_and_recovery(monkeypatch):
    dispatchers: list[_FakeDispatcher] = []

    def fake_dispatcher():
        dp = _FakeDispatcher()
        dispatchers.append(dp)
        return dp

    async def noop_setup_commands(bot):
        return None

    async def fail_recovery(*args, **kwargs):
        raise AssertionError("startup recovery must not run when disabled")

    monkeypatch.setattr(main_mod, "BACKGROUND_JOBS_ENABLED", False)
    monkeypatch.setattr(main_mod, "STARTUP_RECOVERY_ENABLED", False)
    monkeypatch.setattr(main_mod, "Bot", _FakeBot)
    monkeypatch.setattr(main_mod, "Dispatcher", fake_dispatcher)
    monkeypatch.setattr(main_mod, "_setup_commands", noop_setup_commands)
    monkeypatch.setattr(main_mod, "start_scheduler", lambda bot: (_ for _ in ()).throw(
        AssertionError("scheduler must not start when background jobs are disabled")
    ))
    monkeypatch.setattr(main_mod.vault, "ensure_layout", lambda: None)
    monkeypatch.setattr(main_mod.selfcheck, "run", lambda: {})
    monkeypatch.setattr(main_mod.session, "restore_all", lambda: [(123, object())])
    monkeypatch.setattr(main_mod.session, "has_pending", lambda session: True)
    monkeypatch.setattr(main_mod.session, "has_queued", lambda session: True)
    monkeypatch.setattr(main_mod.recovery, "process_pending_on_startup", fail_recovery)
    monkeypatch.setattr(main_mod.recovery, "process_queued_on_startup", fail_recovery)
    monkeypatch.setattr(main_mod.recovery, "process_offline_backlog", fail_recovery)

    await main_mod.main()

    assert dispatchers
    assert dispatchers[0].started
