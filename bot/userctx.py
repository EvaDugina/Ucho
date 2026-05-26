"""Request-scoped «текущий пользователь» (multi-user изоляция).

Бот обслуживает нескольких доверенных пользователей; у каждого — своя
изолированная база в `<vault>/users/<user_id>/`. Чтобы не прокидывать
`user_root` через сигнатуры всего data-слоя, держим его в contextvar.

contextvars **async-безопасны**: каждый aiogram-хэндлер исполняется отдельной
asyncio-задачей, и значение contextvar изолировано per-task — одновременные
пользователи не видят чужой контекст.

Кто обязан выставить контекст (через `set_user`):
- aiogram-middleware на каждый входящий update;
- daily-тикер (по каждому пользователю в цикле);
- startup self-check и session-restore (по каждому пользователю);
- pending-recovery.

`.psycho/` (manifest, log, startup-check, users.json) и git-репо — ГЛОБАЛЬНЫЕ
на корне вольта, не зависят от текущего пользователя.
"""
from __future__ import annotations

import contextvars
from pathlib import Path

from .config import VAULT_PATH

_current_uid: contextvars.ContextVar[int | None] = contextvars.ContextVar(
    "psycho_current_uid", default=None
)


def set_user(uid: int) -> Path:
    """Выставить текущего пользователя. Возвращает его vault-root."""
    _current_uid.set(int(uid))
    return user_root()


def clear_user() -> None:
    """Сбросить request-scoped user context.

    Используется тестами и системными путями, где отсутствие пользователя должно
    быть явным, а не неявным fallback на корень vault.
    """
    _current_uid.set(None)


def current_uid() -> int | None:
    return _current_uid.get()


def system_root() -> Path:
    """Глобальный корень vault (`VAULT_PATH`) для system/migration paths."""
    return VAULT_PATH


def user_root() -> Path:
    """Корень данных текущего пользователя: `<vault>/users/<uid>/`.

    Если uid не выставлен — это ошибка вызова data-layer. Глобальный корень
    берётся только через явный `system_root()`, а доступ к конкретному
    пользователю без переключения контекста — через `root_for(uid)`.
    """
    uid = _current_uid.get()
    if uid is None:
        raise RuntimeError(
            "userctx.user_root() called without current uid; "
            "use userctx.system_root() for vault root or userctx.root_for(uid)"
        )
    return VAULT_PATH / "users" / str(uid)


def root_for(uid: int) -> Path:
    """Vault-root конкретного пользователя без переключения контекста."""
    return VAULT_PATH / "users" / str(uid)
