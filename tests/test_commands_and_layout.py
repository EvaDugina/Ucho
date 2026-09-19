from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from bot import commands, handlers, userctx, vault
from bot.config import OWNER_TELEGRAM_ID


def test_exact_command_list_and_removed_handlers_absent():
    command_names = [item.command for item in commands.BOT_COMMANDS + commands.ADMIN_COMMANDS]
    assert command_names == [
        "pebble",
        "ucho",
        "ask",
        "about",
        "leta",
        "help",
        "start",
        "upload",
        "sea",
        "adduser",
        "removeuser",
        "users",
    ]
    for name in (
        "cmd_echo",
        "cmd_requestion",
        "cmd_history",
        "cmd_cancel",
        "cmd_dailyall",
        "cmd_like",
        "cmd_regen",
        "cmd_remask",
        "cb_face_action",
    ):
        assert not hasattr(handlers, name)


def test_layout_contains_only_current_public_directories(as_user):
    root = userctx.user_root()
    vault.ensure_layout()
    public = sorted(path.name for path in root.iterdir() if not path.name.startswith("_"))
    assert public == ["00_raw", "01_mood", "02_personality"]
    assert (root / "00_raw" / "sessions").is_dir()
    assert (root / "01_mood" / "events").is_dir()
    assert (root / "02_personality" / "about" / "versions").is_dir()
    assert not (root / "02_personality" / "face").exists()
    assert not (root / "01_personality").exists()
    for name in ("qna", "notes", "02_concepts", "02_profile", "03_personality"):
        assert not (root / name).exists()


def test_unknown_command_handler_is_registered_after_known_handlers():
    observers = handlers.router.message.handlers
    callback_names = [item.callback.__name__ for item in observers]
    assert "unknown_command" in callback_names
    assert callback_names.index("unknown_command") > callback_names.index("cmd_sea")


def test_start_registers_commands_after_chat_becomes_available():
    bot = SimpleNamespace(set_my_commands=AsyncMock())
    message = SimpleNamespace(
        bot=bot,
        chat=SimpleNamespace(id=1),
        from_user=SimpleNamespace(id=OWNER_TELEGRAM_ID),
        answer=AsyncMock(),
    )
    asyncio.run(handlers.cmd_start(message))
    bot.set_my_commands.assert_awaited_once()
    sent = bot.set_my_commands.await_args.kwargs
    assert sent["scope"].chat_id == 1
    assert sent["commands"][-1].command == "users"
    message.answer.assert_awaited_once()
