from types import SimpleNamespace

import httpx
import pytest
from openai import APIStatusError

from bot import llm, main
from bot.errors import LLMError


@pytest.fixture(autouse=True)
def reset_billing_alert(monkeypatch):
    llm.set_billing_notifier(None)
    monkeypatch.setattr(llm.vault, "append_log", lambda *args: None)
    yield
    llm.set_billing_notifier(None)


def _api_error(status: int, code: str = "") -> APIStatusError:
    body = {"error": {"code": code, "message": "request rejected"}}
    response = httpx.Response(
        status,
        request=httpx.Request("POST", "https://llm.example/v1/chat/completions"),
        json=body,
    )
    return APIStatusError("request rejected", response=response, body=body)


def _reply(content: str):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


def _stub_client(monkeypatch, results):
    pending = iter(results)

    async def create(**kwargs):
        result = next(pending)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(
        llm,
        "_client",
        SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))),
    )


@pytest.mark.asyncio
async def test_billing_alert_once_until_api_recovers(monkeypatch):
    monkeypatch.setattr(llm, "_models_for", lambda task: ("primary",))
    _stub_client(monkeypatch, [
        _api_error(402), _api_error(402), _reply('{"ok": true}'), _api_error(402),
    ])
    sent = []

    async def notify():
        sent.append("alert")

    llm.set_billing_notifier(notify)
    for _ in range(2):
        with pytest.raises(LLMError):
            await llm._chat_json("ask", [])
    assert sent == ["alert"]

    assert await llm._chat_json("ask", []) == {"ok": True}
    with pytest.raises(LLMError):
        await llm._chat_json("ask", [])
    assert sent == ["alert", "alert"]


@pytest.mark.asyncio
async def test_successful_fallback_does_not_alert(monkeypatch):
    monkeypatch.setattr(llm, "_models_for", lambda task: ("primary", "fallback"))
    _stub_client(monkeypatch, [_api_error(402), _reply("Ответ запасной модели")])
    sent = []

    async def notify():
        sent.append("alert")

    llm.set_billing_notifier(notify)
    assert await llm._chat_text("about", []) == "Ответ запасной модели"
    assert sent == []


@pytest.mark.asyncio
async def test_rate_limit_is_not_billing_but_insufficient_quota_is(monkeypatch):
    monkeypatch.setattr(llm, "_models_for", lambda task: ("primary",))
    _stub_client(monkeypatch, [_api_error(429, "rate_limit_exceeded"),
                               _api_error(429, "insufficient_quota")])
    sent = []

    async def notify():
        sent.append("alert")

    llm.set_billing_notifier(notify)
    with pytest.raises(LLMError):
        await llm._chat_text("about", [])
    assert sent == []
    with pytest.raises(LLMError):
        await llm._chat_text("about", [])
    assert sent == ["alert"]


@pytest.mark.asyncio
async def test_telegram_failure_does_not_hide_llm_error(monkeypatch):
    monkeypatch.setattr(llm, "_models_for", lambda task: ("primary",))
    _stub_client(monkeypatch, [_api_error(402)])

    async def notify():
        raise RuntimeError("telegram unavailable")

    llm.set_billing_notifier(notify)
    with pytest.raises(LLMError):
        await llm._chat_json("ask", [])


@pytest.mark.asyncio
async def test_alert_goes_to_owner_with_exact_text():
    sent = []

    async def send_message(chat_id, text):
        sent.append((chat_id, text))

    await main._send_billing_alert(SimpleNamespace(send_message=send_message))
    assert sent == [(main.OWNER_TELEGRAM_ID, "Я без денег.")]
