"""AITunnel routing tests — no network."""
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


def test_aitunnel_defaults_do_not_use_local_qwen():
    assert config.AITUNNEL_BASE_URL == "https://api.aitunnel.ru/v1"
    assert config.LLM_MODEL_DEFAULT == "qwen3-235b-a22b-2507"
    assert config.LLM_MODEL_FALLBACKS == ("deepseek-v4-flash",)
    assert config.LLM_MODEL_FAST == "deepseek-v4-flash"
    assert config._parse_model_list("deepseek-v4-flash; qwen3-235b-a22b-2507") == (
        "deepseek-v4-flash",
        "qwen3-235b-a22b-2507",
    )


def test_aitunnel_config_rejects_non_aitunnel_values(monkeypatch):
    with pytest.raises(RuntimeError):
        config._aitunnel_base_url("http://localhost:11434/v1")
    with pytest.raises(RuntimeError):
        config._model_from_env("provider/model")
    monkeypatch.delenv("AITUNNEL_API_KEY", raising=False)
    with pytest.raises(RuntimeError):
        config._aitunnel_api_key()


def test_models_for_keeps_primary_then_fallback(monkeypatch):
    monkeypatch.setitem(llm._TASK_ROUTES, "unit", ("primary-model", ("fallback-model",)))
    assert llm._models_for("unit") == ("primary-model", "fallback-model")


@pytest.mark.asyncio
async def test_chat_json_uses_response_format_and_fallback(monkeypatch):
    completions = _FakeCompletions([RuntimeError("primary down"), '{"ok": true}'])
    monkeypatch.setattr(llm, "_client", _FakeClient(completions))
    monkeypatch.setitem(llm._TASK_ROUTES, "unit", ("primary-model", ("fallback-model",)))

    out = await llm._chat_json("unit", [{"role": "user", "content": "x"}], temperature=0.2)

    assert out == {"ok": True}
    assert [c["model"] for c in completions.calls] == ["primary-model", "fallback-model"]
    assert completions.calls[0]["response_format"] == {"type": "json_object"}
    assert "extra_body" not in completions.calls[0]


@pytest.mark.asyncio
async def test_chat_json_falls_back_on_non_json(monkeypatch):
    completions = _FakeCompletions(["not json", '{"ok": true}'])
    monkeypatch.setattr(llm, "_client", _FakeClient(completions))
    monkeypatch.setitem(llm._TASK_ROUTES, "unit", ("primary-model", ("fallback-model",)))

    out = await llm._chat_json("unit", [{"role": "user", "content": "x"}])

    assert out == {"ok": True}
    assert [c["model"] for c in completions.calls] == ["primary-model", "fallback-model"]


@pytest.mark.asyncio
async def test_chat_json_raises_after_all_models_fail(monkeypatch):
    completions = _FakeCompletions([RuntimeError("primary down"), "not json"])
    monkeypatch.setattr(llm, "_client", _FakeClient(completions))
    monkeypatch.setitem(llm._TASK_ROUTES, "unit", ("qwen3-235b-a22b-2507", ("deepseek-v4-flash",)))

    with pytest.raises(LLMError) as exc:
        await llm._chat_json("unit", [{"role": "user", "content": "x"}])
    assert "qwen3-235b-a22b-2507" in exc.value.user_message
    assert "deepseek-v4-flash" in exc.value.user_message


@pytest.mark.asyncio
async def test_regenerate_reaction_ignores_previous_generation_context(monkeypatch):
    captured = {}

    async def fake_chat_text(task, messages, **kwargs):
        captured["task"] = task
        captured["messages"] = messages
        return "новый комментарий"

    monkeypatch.setattr(llm, "_chat_text", fake_chat_text)

    out = await llm.regenerate_reaction(
        "Что болит?",
        "Мой ответ.",
        bot_mood="насмешка",
        session_context="assistant: ПРЕДЫДУЩАЯ ГЕНЕРАЦИЯ, которую нельзя учитывать.",
    )

    prompt = "\n\n".join(m["content"] for m in captured["messages"])
    assert out == "новый комментарий"
    assert captured["task"] == "reaction"
    assert "Мой ответ." in prompt
    assert "насмешка" in prompt
    assert "ПРЕДЫДУЩАЯ ГЕНЕРАЦИЯ" not in prompt

