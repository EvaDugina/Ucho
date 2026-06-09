"""Prompt-контекст для API-анализа 02."""
from __future__ import annotations

import json
from typing import Any

from . import taxonomy


def relevant_categories(target: dict | None, signals: dict | None) -> tuple[str, ...]:
    """Выбрать компактный срез канона для промпта."""
    ordered: list[str] = []
    target_category = (target or {}).get("category")
    if (target or {}).get("area") == taxonomy.AREA_KEY and taxonomy.is_valid_category(target_category):
        ordered.append(str(target_category))
    marker_categories = (signals or {}).get("marker_categories")
    if isinstance(marker_categories, dict):
        for key in marker_categories:
            if taxonomy.is_valid_category(key) and key not in ordered:
                ordered.append(key)
    for category in taxonomy.all_categories():
        if category not in ordered:
            ordered.append(category)
    return tuple(ordered)


def build_taxonomy_context(target: dict | None = None, signals: dict | None = None) -> str:
    lines = [
        f"area: {taxonomy.AREA_KEY} ({taxonomy.AREA_FOLDER})",
        f"description: {taxonomy.AREA_DESCRIPTION}",
        "categories:",
    ]
    for key in relevant_categories(target, signals):
        c = taxonomy.CATEGORY_BY_KEY[key]
        lines.append(f"- {c.key} — {c.title}: {c.description}")
        lines.append("  themes: " + ", ".join(c.themes))
        lines.append("  strong_signs: " + "; ".join(c.strong_signs))
        lines.append("  weak_signs: " + "; ".join(c.weak_signs))
    return "\n".join(lines)


def format_signals(signals: dict[str, Any]) -> str:
    return json.dumps(signals, ensure_ascii=False, sort_keys=True)
