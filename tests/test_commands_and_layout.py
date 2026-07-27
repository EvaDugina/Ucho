from __future__ import annotations

from bot import handlers, main, userctx, vault


def test_exact_command_list_and_removed_handlers_absent():
    commands = [item.command for item in main.BOT_COMMANDS + main.ADMIN_COMMANDS]
    assert commands == [
        "pebble",
        "ucho",
        "ask",
        "about",
        "regen",
        "like",
        "remask",
        "leta",
        "help",
        "start",
        "upload",
        "sea",
        "adduser",
        "removeuser",
        "users",
    ]
    for name in ("cmd_echo", "cmd_requestion", "cmd_history", "cmd_cancel", "cmd_dailyall"):
        assert not hasattr(handlers, name)


def test_layout_contains_only_current_public_directories(as_user):
    root = userctx.user_root()
    vault.ensure_layout()
    public = sorted(path.name for path in root.iterdir() if not path.name.startswith("_"))
    assert public == ["00_raw", "01_mood", "01_personality"]
    assert (root / "00_raw" / "sessions").is_dir()
    assert (root / "01_mood" / "events").is_dir()
    assert (root / "01_personality" / "about" / "versions").is_dir()
    assert (root / "01_personality" / "face").is_dir()
    for name in ("qna", "notes", "02_concepts", "02_profile", "03_personality"):
        assert not (root / name).exists()


def test_unknown_command_handler_is_registered_after_known_handlers():
    observers = handlers.router.message.handlers
    callback_names = [item.callback.__name__ for item in observers]
    assert "unknown_command" in callback_names
    assert callback_names.index("unknown_command") > callback_names.index("cmd_sea")
