from bot.validation import (
    safe_chat_html,
    safe_question_text,
    safe_user_text,
)


def test_user_text_normalizes_controls_and_truncates():
    value, truncated = safe_user_text("a\r\nb\x00cde", limit=4)
    assert value == "a\nbc…"
    assert truncated is True


def test_question_is_one_line():
    assert safe_question_text("Что\n\nважно?") == "Что важно?"


def test_chat_html_is_escaped():
    assert safe_chat_html("<b>x</b> & y") == "&lt;b&gt;x&lt;/b&gt; &amp; y"
