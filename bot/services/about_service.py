"""Двухшаговая сборка и нейтральное представление personality-профиля."""
from __future__ import annotations

from datetime import datetime

from .. import about, llm, vault


async def refresh_and_present(*, at: datetime | None = None) -> tuple[str, str, str | None]:
    """Вернуть пересказ, полный профиль и ID новой версии."""
    current = about.current_profile()
    pending = about.pending_deltas()
    version_id: str | None = None
    if pending:
        synthesized = await llm.synthesize_about(current, pending)
        with vault.user_write("personality synthesis"):
            version_id = about.save_synthesis(
                synthesized,
                [str(item["id"]) for item in pending],
                at=at,
            )
        current = synthesized
    if not current.strip():
        return "", "", version_id
    return await llm.about_present(current), current, version_id
