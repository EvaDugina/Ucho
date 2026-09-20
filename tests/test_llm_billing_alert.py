from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from openai import APIStatusError

from bot import llm, main, recovery, userctx
from bot.errors import LLMError


@pytest.fixture(autouse=True)
def reset_billing_alert(monkeypatch, tmp_path):
    monkeypatch.setattr(llm, "_billing_alert_state_path", tmp_path / "billing-alert.json")
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
        with pytest.raises(LLMError) as caught:
            await llm._chat_json("ask", [])
        assert caught.value.billing is True
        assert caught.value.user_message == "Я без денег."
    assert sent == ["alert"]

    assert await llm._chat_json("ask", []) == {"ok": True}
    llm.set_billing_notifier(None)
    llm.set_billing_notifier(notify)
    with pytest.raises(LLMError):
        await llm._chat_json("ask", [])
    assert sent == ["alert", "alert"]


@pytest.mark.asyncio
async def test_guest_billing_error_alerts_owner_once_even_after_restart(monkeypatch):
    monkeypatch.setattr(llm, "_models_for", lambda task: ("primary",))
    _stub_client(monkeypatch, [_api_error(402), _api_error(402), _api_error(402)])
    sent = []

    async def send_message(chat_id, text):
        sent.append((chat_id, text))

    bot = SimpleNamespace(send_message=send_message)
    llm.set_billing_notifier(lambda: main._send_billing_alert(bot))
    userctx.set_user(main.OWNER_TELEGRAM_ID + 1)
    for _ in range(2):
        with pytest.raises(LLMError):
            await llm._chat_json("ask", [])
    llm.set_billing_notifier(None)
    llm.set_billing_notifier(lambda: main._send_billing_alert(bot))
    with pytest.raises(LLMError):
        await llm._chat_json("ask", [])
    assert sent == [(main.OWNER_TELEGRAM_ID, "Я без денег.")]


@pytest.mark.asyncio
async def test_other_model_success_does_not_rearm_billing_alert(monkeypatch):
    monkeypatch.setattr(
        llm, "_models_for", lambda task: ("primary",) if task == "ask" else ("other",),
    )
    _stub_client(monkeypatch, [_api_error(402), _reply("Другой ответ"), _api_error(402)])
    sent = []

    async def notify():
        sent.append("alert")

    llm.set_billing_notifier(notify)
    with pytest.raises(LLMError):
        await llm._chat_json("ask", [])
    assert await llm._chat_text("about", []) == "Другой ответ"
    with pytest.raises(LLMError):
        await llm._chat_json("ask", [])
    assert sent == ["alert"]


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
    _stub_client(monkeypatch, [_api_error(402), _api_error(402)])
    attempts = []

    async def notify():
        attempts.append("attempt")
        raise RuntimeError("telegram unavailable")

    llm.set_billing_notifier(notify)
    for _ in range(2):
        with pytest.raises(LLMError):
            await llm._chat_json("ask", [])
    assert attempts == ["attempt", "attempt"]
    assert not llm._billing_alert_state_path.exists()


@pytest.mark.asyncio
async def test_alert_goes_to_owner_with_exact_text():
    sent = []

    async def send_message(chat_id, text):
        sent.append((chat_id, text))

    await main._send_billing_alert(SimpleNamespace(send_message=send_message))
    assert sent == [(main.OWNER_TELEGRAM_ID, "Я без денег.")]


@pytest.mark.asyncio
async def test_recovery_replies_to_requester_on_billing_error():
    bot = SimpleNamespace(send_message=AsyncMock())
    error = LLMError("payment required", billing=True)

    await recovery._reply_billing_failure(bot, 42, error)

    bot.send_message.assert_awaited_once_with(42, "Я без денег.")
