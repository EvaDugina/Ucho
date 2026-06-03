"""LLM provider routing tests — no network."""
from __future__ import annotations

import importlib

import pytest

from bot import config, llm, userctx
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
    assert config.LLM_PROVIDER_NAME == "AITunnel"
    assert config.AITUNNEL_BASE_URL == "https://api.aitunnel.ru/v1"
    assert config.LLM_BASE_URL == "https://api.aitunnel.ru/v1"
    assert config.LLM_MODEL_DEFAULT == "qwen3-235b-a22b-2507"
    assert config.LLM_MODEL_FALLBACKS == ("deepseek-v4-flash",)
    assert config.LLM_MODEL_FAST == "deepseek-v4-flash"
    assert config._parse_model_list("deepseek-v4-flash; qwen3-235b-a22b-2507") == (
        "deepseek-v4-flash",
        "qwen3-235b-a22b-2507",
    )


def test_openrouter_priority_allows_provider_model_ids(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-openrouter-key")
    monkeypatch.setenv("OPENROUTER_HTTP_REFERER", "https://example.test")
    monkeypatch.setenv("OPENROUTER_APP_TITLE", "Psycho Test")
    monkeypatch.delenv("AITUNNEL_API_KEY", raising=False)
    try:
        importlib.reload(config)
        importlib.reload(llm)
        assert config.LLM_PROVIDER_NAME == "OpenRouter"
        assert config.LLM_API_KEY == "test-openrouter-key"
        assert config.LLM_BASE_URL == "https://openrouter.ai/api/v1"
        assert config.AITUNNEL_API_KEY == ""
        assert config.LLM_DEFAULT_HEADERS == {
            "HTTP-Referer": "https://example.test",
            "X-OpenRouter-Title": "Psycho Test",
        }
        assert config.LLM_MODEL_DEFAULT == "qwen/qwen3-235b-a22b-2507"
        assert config.LLM_MODEL_FALLBACKS == ("deepseek/deepseek-v4-flash",)
        assert config._model_from_env(
            "qwen/qwen3-235b-a22b-2507",
            provider="openrouter",
        ) == "qwen/qwen3-235b-a22b-2507"
    finally:
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        monkeypatch.delenv("OPENROUTER_HTTP_REFERER", raising=False)
        monkeypatch.delenv("OPENROUTER_APP_TITLE", raising=False)
        monkeypatch.setenv("AITUNNEL_API_KEY", "test-aitunnel-key")
        importlib.reload(config)
        importlib.reload(llm)


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
async def test_remind_presence_uses_primary_and_user_prompt(as_user, monkeypatch):
    prompt_file = userctx.user_root() / "03_personality" / "user_prompt.md"
    prompt_file.parent.mkdir(parents=True, exist_ok=True)
    prompt_file.write_text("говори тише и ближе", encoding="utf-8")
    completions = _FakeCompletions(["Я рядом"])
    monkeypatch.setattr(llm, "_client", _FakeClient(completions))
    monkeypatch.setitem(llm._TASK_ROUTES, "reaction", ("primary-model", ("unused-fallback",)))
    monkeypatch.setitem(llm._TASK_ROUTES, "fast", ("deepseek-v4-flash", ()))

    out = await llm.remind_presence("Что ты не сказал сегодня?", bot_mood="вера")

    assert out == "Я рядом"
    assert [c["model"] for c in completions.calls] == ["primary-model"]
    system_prompt = completions.calls[0]["messages"][0]["content"]
    assert "говори тише и ближе" in system_prompt


@pytest.mark.asyncio
async def test_remind_presence_retries_fast_after_primary_error(as_user, monkeypatch):
    completions = _FakeCompletions([RuntimeError("primary down"), "Я рядом"])
    monkeypatch.setattr(llm, "_client", _FakeClient(completions))
    monkeypatch.setitem(llm._TASK_ROUTES, "reaction", ("primary-model", ("unused-fallback",)))
    monkeypatch.setitem(llm._TASK_ROUTES, "fast", ("deepseek-v4-flash", ()))

    out = await llm.remind_presence("Что ты не сказал сегодня?", bot_mood="вера")

    assert out == "Я рядом"
    assert [c["model"] for c in completions.calls] == ["primary-model", "deepseek-v4-flash"]


@pytest.mark.asyncio
async def test_remind_presence_retries_fast_after_empty_primary(as_user, monkeypatch):
    completions = _FakeCompletions(["", "Я рядом"])
    monkeypatch.setattr(llm, "_client", _FakeClient(completions))
    monkeypatch.setitem(llm._TASK_ROUTES, "reaction", ("primary-model", ("unused-fallback",)))
    monkeypatch.setitem(llm._TASK_ROUTES, "fast", ("deepseek-v4-flash", ()))

    out = await llm.remind_presence("Что ты не сказал сегодня?", bot_mood="вера")

    assert out == "Я рядом"
    assert [c["model"] for c in completions.calls] == ["primary-model", "deepseek-v4-flash"]


@pytest.mark.asyncio
async def test_remind_presence_raises_when_primary_and_fast_empty(as_user, monkeypatch):
    completions = _FakeCompletions(["", ""])
    monkeypatch.setattr(llm, "_client", _FakeClient(completions))
    monkeypatch.setitem(llm._TASK_ROUTES, "reaction", ("primary-model", ("unused-fallback",)))
    monkeypatch.setitem(llm._TASK_ROUTES, "fast", ("deepseek-v4-flash", ()))

    with pytest.raises(LLMError):
        await llm.remind_presence("Что ты не сказал сегодня?", bot_mood="вера")
    assert [c["model"] for c in completions.calls] == ["primary-model", "deepseek-v4-flash"]


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


@pytest.mark.asyncio
async def test_only_generated_comments_drop_commas(as_user, monkeypatch):
    async def fake_chat_json(task, messages, **kwargs):
        if task == "ask":
            return {"domain": "everyday", "question": "Жив, да? Или нет!"}
        return {"observations": [], "reaction": "Ну, вот! Да?", "user_delta": {}}

    monkeypatch.setattr(llm, "_chat_json", fake_chat_json)

    question = await llm.ask_next(domain="everyday")
    reaction = await llm.process_answer(
        question="Что важно?",
        answer="Ответ.",
        domain_hint="everyday",
    )

    assert question["question"] == "Жив, да? Или нет!"
    assert reaction["reaction"] == "Ну вот Да?"


@pytest.mark.asyncio
async def test_about_present_preserves_punctuation(monkeypatch):
    async def fake_chat_text(task, messages, **kwargs):
        return "Ты, значит: живой!"

    monkeypatch.setattr(llm, "_chat_text", fake_chat_text)

    assert await llm.about_present("портрет") == "Ты, значит: живой!"
