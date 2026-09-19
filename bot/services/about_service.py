"""Нейтральная сборка personality-профиля и представление голосом Иуды."""
from __future__ import annotations

from datetime import datetime

from .. import about, llm, vault
from . import mood_service


async def refresh_and_present(*, at: datetime | None = None) -> tuple[str, str, str | None]:
    """Вернуть пересказ, полный профиль и ID новой версии."""
    mood_status = await mood_service.refresh_from_latest(at=at)
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
        current = about.current_profile()
    if not current.strip():
        return "", "", version_id
    spoken = await llm.about_present(current)
    if mood_status == "unavailable":
        spoken += "\n\nМне не удалось обновить оценку настроения; предыдущая запись сохранена."
    return spoken, current, version_id
