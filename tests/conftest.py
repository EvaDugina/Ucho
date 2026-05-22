"""Общие фикстуры тестов.

КРИТИЧНО: env-переменные выставляются ДО импорта пакета ``bot`` — ``config.py``
читает их на этапе импорта (``VAULT_PATH``, ``TELEGRAM_BOT_TOKEN``,
``OWNER_TELEGRAM_ID``), а ``userctx``/``vault`` биндят ``VAULT_PATH`` к себе при
импорте. Поэтому conftest подменяет окружение на уровне модуля, до любого
``from bot import ...`` (conftest импортируется pytest-ом раньше тест-модулей).

Тесты гоняются ТОЛЬКО в Docker (правило репозитория) — git в образе есть,
поэтому транзакционные тесты ``git_wrap`` работоспособны.
"""
from __future__ import annotations

import itertools
import os
import tempfile

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token")
os.environ.setdefault("OWNER_TELEGRAM_ID", "1")
# Общий на сессию временный вольт; изоляция между тестами — через уникальный uid.
os.environ["VAULT_PATH"] = tempfile.mkdtemp(prefix="psycho-test-vault-")

import pytest  # noqa: E402

from bot import userctx, vault  # noqa: E402

# Уникальный uid на каждый тест → свой подкаталог users/<uid>/ в общем вольте.
_uid_counter = itertools.count(1000)


@pytest.fixture
def as_user() -> int:
    """Выставить уникального пользователя и создать его layout + git-репо вольта.

    Возвращает uid. Каждый тест получает чистое поддерево ``users/<uid>/`` —
    данные тестов не пересекаются.
    """
    uid = next(_uid_counter)
    userctx.set_user(uid)
    vault.ensure_layout()  # создаёт users/<uid>/ и (идемпотентно) git-репо на корне
    return uid
