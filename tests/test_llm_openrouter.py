"""OpenRouter routing tests — no network."""
from __future__ import annotations

import pytest

from bot import config, llm
from bot.errors import LLMError


class _Resp:
    def __init__(self, content: str):
        self.choices = [type("Choice", (), {"message": type("Msg", (), {"content": content})()})()]


class _FakeCompletions:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return _Resp(item)


class _FakeClient:
    def __init__(self, completions: _FakeCompletions):
        self.chat = type("Chat", (), {"completions": completions})()


def test_openrouter_defaults_do_not_use_local_qwen():
    assert config.OPENAI_BASE_URL == "https://openrouter.ai/api/v1"
    assert config.LLM_MODEL_DEFAULT == "qwen/qwen3-235b-a22b-2507"
    assert config.LLM_MODEL_FALLBACKS == ("deepseek/deepseek-v4-flash",)
    assert config._parse_model_list("deepseek/deepseek-v4-flash; qwen/qwen3.5-flash-02-23") == (
        "deepseek/deepseek-v4-flash",
        "qwen/qwen3.5-flash-02-23",
    )


def test_openrouter_config_rejects_non_openrouter_values(monkeypatch):
    with pytest.raises(RuntimeError):
        config._openrouter_base_url("http://localhost:11434/v1")
    with pytest.raises(RuntimeError):
        config._model_from_env("local-model")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(RuntimeError):
        config._openrouter_api_key()


def test_models_for_keeps_primary_then_fallback(monkeypatch):
    monkeypatch.setitem(llm._TASK_ROUTES, "unit", ("primary-model", ("fallback-model",)))
    assert llm._models_for("unit") == ("primary-model", "fallback-model")


def test_openrouter_privacy_body():
    body = llm._openrouter_extra_body()
    assert body["provider"]["data_collection"] == "deny"
    assert body["provider"]["zdr"] is True


@pytest.mark.asyncio
async def test_chat_json_uses_response_format_and_fallback(monkeypatch):
    completions = _FakeCompletions([RuntimeError("primary down"), '{"ok": true}'])
    monkeypatch.setattr(llm, "_client", _FakeClient(completions))
    monkeypatch.setitem(llm._TASK_ROUTES, "unit", ("primary-model", ("fallback-model",)))

    out = await llm._chat_json("unit", [{"role": "user", "content": "x"}], temperature=0.2)

    assert out == {"ok": True}
    assert [c["model"] for c in completions.calls] == ["primary-model", "fallback-model"]
    assert completions.calls[0]["response_format"] == {"type": "json_object"}
    assert completions.calls[0]["extra_body"]["provider"]["data_collection"] == "deny"


@pytest.mark.asyncio
async def test_chat_json_falls_back_on_non_json(monkeypatch):
    completions = _FakeCompletions(["not json", '{"ok": true}'])
    monkeypatch.setattr(llm, "_client", _FakeClient(completions))
    monkeypatch.setitem(llm._TASK_ROUTES, "unit", ("primary-model", ("fallback-model",)))

    out = await llm._chat_json("unit", [{"role": "user", "content": "x"}])

    assert out == {"ok": True}
    assert [c["model"] for c in completions.calls] == ["primary-model", "fallback-model"]


@pytest.mark.asyncio
async def test_chat_json_retries_same_model_without_zdr(monkeypatch):
    completions = _FakeCompletions([
        RuntimeError("No endpoints found matching your data policy (Zero data retention)"),
        '{"ok": true}',
    ])
    monkeypatch.setattr(llm, "_client", _FakeClient(completions))
    monkeypatch.setitem(llm._TASK_ROUTES, "unit", ("primary-model", ()))

    out = await llm._chat_json("unit", [{"role": "user", "content": "x"}])

    assert out == {"ok": True}
    assert [c["model"] for c in completions.calls] == ["primary-model", "primary-model"]
    assert completions.calls[0]["extra_body"]["provider"]["zdr"] is True
    assert "zdr" not in completions.calls[1]["extra_body"]["provider"]


@pytest.mark.asyncio
async def test_chat_json_raises_after_all_models_fail(monkeypatch):
    completions = _FakeCompletions([RuntimeError("primary down"), "not json"])
    monkeypatch.setattr(llm, "_client", _FakeClient(completions))
    monkeypatch.setitem(llm._TASK_ROUTES, "unit", ("qwen/qwen3-235b-a22b-2507", ("deepseek/deepseek-v4-flash",)))

    with pytest.raises(LLMError) as exc:
        await llm._chat_json("unit", [{"role": "user", "content": "x"}])
    assert "qwen/qwen3-235b-a22b-2507" in exc.value.user_message
    assert "deepseek/deepseek-v4-flash" in exc.value.user_message
