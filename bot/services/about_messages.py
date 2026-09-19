"""Читаемое и безопасное HTML-представление полного /about для Telegram."""
from __future__ import annotations

import re

from ..about import normalize_profile
from ..validation import safe_chat_html
from .session_messages import TG_MSG_LIMIT

_BODY_LIMIT = TG_MSG_LIMIT - 100  # место для заголовка и номера части
_HEADING_RE = re.compile(r"^#{1,6}\s+(.+)$")


def _blocks(profile: str) -> list[tuple[str, str]]:
    lines = normalize_profile(profile).split("\n")
    blocks: list[tuple[str, str]] = []
    start = 0
    if lines and lines[0].strip() == "---":
        end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
        if end is not None:
            blocks.append(("pre", "\n".join(lines[: end + 1])))
            start = end + 1

    paragraph: list[str] = []

    def flush() -> None:
        if paragraph:
            blocks.append(("text", "\n".join(paragraph)))
            paragraph.clear()

    for line in lines[start:]:
        clean = line.strip()
        if not clean:
            flush()
            continue
        heading = _HEADING_RE.fullmatch(clean)
        if heading:
            flush()
            blocks.append(("heading", heading.group(1).strip()))
        elif clean.startswith("- "):
            flush()
            blocks.append(("text", "• " + clean[2:].strip()))
        else:
            paragraph.append(clean)
    flush()
    return blocks


def _render(kind: str, text: str) -> str:
    safe = safe_chat_html(text)
    if kind == "heading":
        return f"<b>{safe}</b>"
    if kind == "pre":
        return f"<pre>{safe}</pre>"
    return safe


def _pieces(kind: str, text: str) -> list[str]:
    """Делить до HTML-экранирования, чтобы каждый фрагмент закрыл свои теги."""
    wrapper = 9 if kind == "heading" else 11 if kind == "pre" else 0
    budget = _BODY_LIMIT - wrapper
    pieces: list[str] = []
    rest = text
    while rest:
        used = 0
        cut = 0
        preferred = 0
        for index, char in enumerate(rest):
            width = len(safe_chat_html(char))
            if used + width > budget:
                break
            used += width
            cut = index + 1
            if char.isspace():
                preferred = cut
        if cut == 0:
            raise ValueError("Telegram HTML character exceeds message budget")
        if cut < len(rest) and preferred >= cut // 2:
            cut = preferred
        pieces.append(_render(kind, rest[:cut]))
        rest = rest[cut:]
    return pieces


def format_full_profile(profile: str) -> list[str]:
    """Вернуть полную Markdown-версию как отдельные валидные HTML-сообщения."""
    if not profile.strip():
        return []
    bodies: list[str] = []
    current = ""
    for kind, text in _blocks(profile):
        for piece in _pieces(kind, text):
            candidate = f"{current}\n\n{piece}" if current else piece
            if len(candidate) > _BODY_LIMIT and current:
                bodies.append(current)
                current = piece
            else:
                current = candidate
    if current:
        bodies.append(current)
    total = len(bodies)
    result = []
    for index, body in enumerate(bodies, 1):
        heading = (
            "Полная версия описания"
            if index == 1 else f"Продолжение описания · {index}/{total}"
        )
        result.append(f"<b>{heading}</b>\n\n{body}")
    return result
