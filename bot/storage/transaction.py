"""Проверка области записи без Git-транзакций."""
from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from .. import userctx
from ..errors import VaultError


@contextmanager
def user_write(op_name: str) -> Iterator[None]:
    """Не разрешать пользовательскую запись без явного uid в контексте."""
    if userctx.current_uid() is None:
        raise VaultError(f"{op_name}: user-scoped write requires current uid")
    yield


@contextmanager
def books_write(_op_name: str) -> Iterator[None]:
    """Общая библиотека не зависит от пользовательского контекста."""
    yield
