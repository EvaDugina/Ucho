from __future__ import annotations

from bot import config


def test_env_bool_blank_keeps_default(monkeypatch):
    monkeypatch.setenv("BOOL_FLAG", "")

    assert config._env_bool("BOOL_FLAG", True) is True
    assert config._env_bool("BOOL_FLAG", False) is False


def test_env_bool_explicit_false_overrides_default(monkeypatch):
    monkeypatch.setenv("BOOL_FLAG", "false")

    assert config._env_bool("BOOL_FLAG", True) is False
