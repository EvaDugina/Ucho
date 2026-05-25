"""Иерархия доменных исключений бота.

Раньше всё ловилось как голый ``Exception`` — это маскировало баги (логическая
ошибка в коде неотличима от ожидаемого сбоя LLM/диска). Эти классы дают точечную
обработку там, где природа сбоя известна; на верхней границе хэндлера и в
глобальном error-handler по-прежнему допустим широкий перехват.

* ``LLMError`` — обращение к модели не удалось или ответ не разобрать.
* ``VaultError`` — сбой записи/git в файловом хранилище.
* ``ValidationError`` — ввод или контракт данных не прошёл проверку.
"""
from __future__ import annotations


class PsychoError(Exception):
    """Базовый класс всех доменных ошибок бота."""


class LLMError(PsychoError):
    """Сбой обращения к LLM или некорректный/неразбираемый ответ модели."""

    def __init__(self, message: str, *, user_message: str | None = None):
        super().__init__(message)
        self.user_message = user_message or "Модели OpenRouter сейчас недоступны. Попробуй позже."


class VaultError(PsychoError):
    """Сбой записи или git-операции в vault."""


class ValidationError(PsychoError):
    """Ввод или контракт данных не прошёл валидацию."""
