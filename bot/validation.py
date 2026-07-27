"""Санитизация принятого текста и динамического Telegram HTML."""
from __future__ import annotations

import html
import re

MAX_USER_TEXT = 10_000
MAX_QUESTION_TEXT = 2_000
_COMMENT_KEEP_PUNCT = {".", "?"}


def _truncate(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    head = value[:limit]
    space = head.rfind(" ", max(0, limit - 50))
    if space > 0:
        head = head[:space]
    return head.rstrip() + "…"


def safe_user_text(raw: str, limit: int = MAX_USER_TEXT) -> tuple[str, bool]:
    """Нормализовать текст, сохранив переносы, и вернуть флаг усечения."""
    if not raw:
        return "", False
    value = str(raw).replace("\r\n", "\n").replace("\r", "\n")
    value = "".join(
        char
        for char in value
        if char in {"\n", "\t"} or (ord(char) >= 0x20 and ord(char) != 0x7F)
    )
    truncated = len(value) > limit
    return (_truncate(value, limit) if truncated else value).strip(), truncated


def safe_question_text(raw: str) -> str:
    """Свести LLM-вопрос к одной строке ограниченной длины."""
    value, _ = safe_user_text(raw, limit=MAX_QUESTION_TEXT)
    return re.sub(r"\s+", " ", value).strip()


def strip_comment_punctuation(text: str) -> str:
    """Оставить в реакции буквы, цифры, пробелы и `. ?`."""
    if not text:
        return ""
    value = str(text).replace("…", ".")
    value = "".join(
        char
        if char.isalnum() or char.isspace() or char in _COMMENT_KEEP_PUNCT
        else " "
        for char in value
    )
    value = re.sub(r"[^\S\n]+", " ", value)
    value = re.sub(r"\s+([.?])", r"\1", value)
    return "\n".join(line.strip() for line in value.splitlines()).strip()


def safe_chat_html(text: str) -> str:
    """Показать LLM-вывод как текст, не как Telegram HTML."""
    if not text:
        return ""
    value = str(text).replace("\r\n", "\n").replace("\r", "\n")
    value = "".join(
        char
        for char in value
        if char in {"\n", "\t"} or (ord(char) >= 0x20 and ord(char) != 0x7F)
    )
    return html.escape(value)
