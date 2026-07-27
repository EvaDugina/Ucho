"""Двухшаговая сборка и озвучивание personality-профиля."""
from __future__ import annotations

from datetime import datetime

from .. import about, llm, vault


async def refresh_and_present(*, at: datetime | None = None) -> tuple[str, str | None]:
    """Вернуть озвученный профиль и ID новой версии, если были pending-дельты."""
    current = about.current_profile()
    pending = about.pending_deltas()
    version_id: str | None = None
    if pending:
        synthesized = await llm.synthesize_about(current, pending)
        with vault.git_wrap("personality synthesis"):
            version_id = about.save_synthesis(
                synthesized,
                [str(item["id"]) for item in pending],
                at=at,
            )
        current = synthesized
    if not current.strip():
        return "", version_id
    return await llm.about_present(current), version_id
