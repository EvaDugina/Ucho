from __future__ import annotations

import io
import logging

from bot.logging_setup import configure_logging


def test_configure_logging_writes_stream_and_container_file(tmp_path, monkeypatch):
    root = logging.getLogger()
    previous_handlers = list(root.handlers)
    previous_level = root.level
    stream = io.StringIO()

    monkeypatch.setenv("CONTAINER_LOG_DIR", str(tmp_path))
    monkeypatch.setenv("CONTAINER_LOG_MAX_BYTES", "1024")
    monkeypatch.setenv("CONTAINER_LOG_BACKUP_COUNT", "1")

    try:
        configure_logging("INFO", stream=stream)
        logging.getLogger("psycho.test").info("file log marker")
        for handler in root.handlers:
            handler.flush()

        assert "file log marker" in stream.getvalue()
        assert "file log marker" in (tmp_path / "bot.log").read_text(encoding="utf-8")
    finally:
        for handler in list(root.handlers):
            root.removeHandler(handler)
            handler.close()
        for handler in previous_handlers:
            root.addHandler(handler)
        root.setLevel(previous_level)


def test_configure_logging_without_log_dir_keeps_file_logging_disabled(tmp_path, monkeypatch):
    root = logging.getLogger()
    previous_handlers = list(root.handlers)
    previous_level = root.level
    stream = io.StringIO()

    monkeypatch.delenv("CONTAINER_LOG_DIR", raising=False)
    monkeypatch.chdir(tmp_path)

    try:
        configure_logging("INFO", stream=stream)
        logging.getLogger("psycho.test").info("stream only marker")
        for handler in root.handlers:
            handler.flush()

        assert "stream only marker" in stream.getvalue()
        assert not (tmp_path / "bot.log").exists()
    finally:
        for handler in list(root.handlers):
            root.removeHandler(handler)
            handler.close()
        for handler in previous_handlers:
            root.addHandler(handler)
        root.setLevel(previous_level)
