"""Юнит-тесты санитизации/валидации (чистые функции, без vault)."""
from __future__ import annotations

from bot import validation as v


def test_safe_slug_path_traversal_neutralized():
    out = v.safe_slug("../../etc/passwd")
    assert "/" not in out
    assert ".." not in out
    assert out == "etc-passwd"


def test_safe_slug_empty_for_garbage():
    assert v.safe_slug("...") == ""
    assert v.safe_slug("///") == ""
    assert v.safe_slug("") == ""


def test_safe_slug_drops_non_ascii():
    # Кириллица не транслитерируется в safe_slug (это делает slugify) → пусто.
    assert v.safe_slug("Привет") == ""


def test_safe_slug_truncates_to_limit():
    out = v.safe_slug("a" * 200)
    assert len(out) <= v.MAX_SLUG_LEN


def test_slugify_translit():
    assert v.slugify("Честность слова") == "chestnost-slova"


def test_escape_raw_block_neutralizes_headers():
    out = v.escape_raw_block("## Q42 · подделка")
    assert out.startswith("​")
    out2 = v.escape_raw_block("**A:** подделка")
    assert "​" in out2


def test_safe_summary_strips_frontmatter_delim():
    out = v.safe_summary("строка1\n---\nстрока2")
    assert "\n---\n" not in out
    assert "———" in out


def test_safe_chat_html_escapes_markup():
    assert v.safe_chat_html("<b>hi</b>") == "&lt;b&gt;hi&lt;/b&gt;"


def test_safe_user_text_truncation_flag():
    text, truncated = v.safe_user_text("a" * (v.MAX_USER_TEXT + 100))
    assert truncated is True
    assert len(text) <= v.MAX_USER_TEXT + 1  # +1 на символ «…»


def test_safe_user_text_strips_control_bytes():
    text, _ = v.safe_user_text("привет\x00мир")
    assert "\x00" not in text


def test_is_valid_telegram_command_arg():
    assert v.is_valid_telegram_command_arg("ethics") is True
    assert v.is_valid_telegram_command_arg("../x") is False
    assert v.is_valid_telegram_command_arg("") is False
    assert v.is_valid_telegram_command_arg("a;b") is False
