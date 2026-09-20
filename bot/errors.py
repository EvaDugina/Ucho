"""Иерархия доменных исключений бота.

Раньше всё ловилось как голый ``Exception`` — это маскировало баги (логическая
ошибка в коде неотличима от ожидаемого сбоя LLM/диска). Эти классы дают точечную
обработку там, где природа сбоя известна; на верхней границе хэндлера и в
глобальном error-handler по-прежнему допустим широкий перехват.

* ``LLMError`` — обращение к модели не удалось или ответ не разобрать.
* ``VaultError`` — сбой записи в файловом хранилище.
* ``ValidationError`` — ввод или контракт данных не прошёл проверку.
"""
from __future__ import annotations

BILLING_MESSAGE = "Я без денег."


class UchoError(Exception):
    """Базовый класс всех доменных ошибок бота."""


class LLMError(UchoError):
    """Сбой обращения к LLM или некорректный/неразбираемый ответ модели."""

    def __init__(
        self, message: str, *, user_message: str | None = None, billing: bool = False,
    ):
        super().__init__(message)
        self.billing = billing
        self.user_message = (
            BILLING_MESSAGE if billing
            else user_message or "LLM-провайдер сейчас недоступен. Попробуй позже."
        )


class VaultError(UchoError):
    """Сбой записи в файловом хранилище."""


class ValidationError(UchoError):
    """Ввод или контракт данных не прошёл валидацию."""
