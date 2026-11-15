"""Provider adapters (OpenAI, Anthropic, Ollama) against fake clients: what each sends, what
it returns, and that errors never carry the deployment name. No network, no tokens."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import anthropic
import httpx
import openai
import pytest

from axiom.router.adapters import (
    ANTHROPIC_MIN_MAX_TOKENS,
    AnthropicAdapter,
    OllamaAdapter,
    OpenAIAdapter,
    anthropic_client_from_env,
    ollama_client_from_env,
)
from axiom.router.router import ADAPTERS, AliasTarget, ModelRouter, RoutingPolicy, _default_factory
from axiom.router.types import ChatMessage, ModelRequest, ProviderError

DEPLOYMENT = "secret-deployment-name"


def request(json_output: bool = True) -> ModelRequest:
    return ModelRequest(
        route="narrative",
        messages=(
            ChatMessage(role="system", content="You draft credit memos."),
            ChatMessage(role="user", content="Draft pkg-04."),
        ),
        prompt_id="drafter",
        prompt_version="v3",
        recording_key="drafter/v3/pkg-04",
        json_output=json_output,
        max_output_tokens=4000,
    )


class Recorder:
    """Stands in for an SDK method: records the call, then returns or raises."""

    def __init__(self, result: Any = None, error: Exception | None = None) -> None:
        self.result, self.error = result, error
        self.kwargs: dict[str, Any] = {}

    async def __call__(self, **kwargs: Any) -> Any:
        self.kwargs = kwargs
        if self.error is not None:
            raise self.error
        return self.result


def openai_client(create: Recorder) -> Any:
    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))


def openai_result(text: str) -> Any:
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=text))],
        usage=SimpleNamespace(prompt_tokens=11, completion_tokens=7),
    )


def anthropic_client(create: Recorder) -> Any:
    return SimpleNamespace(messages=SimpleNamespace(create=create))


def anthropic_result(text: str, stop_reason: str = "end_turn") -> Any:
    return SimpleNamespace(
        content=[
            SimpleNamespace(type="thinking", thinking=""),
            SimpleNamespace(type="text", text=text),
        ],
        stop_reason=stop_reason,
        usage=SimpleNamespace(input_tokens=13, output_tokens=5),
    )


_REQ = httpx.Request("POST", "https://provider.example.test")


# OpenAI and Ollama -------------------------------------------------------------------------


def test_openai_sends_json_mode_and_reads_usage() -> None:
    create = Recorder(openai_result('{"ok": true}'))
    response = asyncio.run(OpenAIAdapter(openai_client(create)).complete(request(), DEPLOYMENT))
    assert create.kwargs["model"] == DEPLOYMENT
    assert create.kwargs["response_format"] == {"type": "json_object"}
    assert create.kwargs["max_completion_tokens"] == 4000
    assert (response.text, response.input_tokens, response.output_tokens) == ('{"ok": true}', 11, 7)
    assert response.origin == "live"


def test_ollama_uses_the_openai_compatible_path_with_max_tokens() -> None:
    create = Recorder(openai_result("{}"))
    asyncio.run(OllamaAdapter(openai_client(create)).complete(request(), "llama-local"))
    assert create.kwargs["max_tokens"] == 4000 and "max_completion_tokens" not in create.kwargs


def test_ollama_client_needs_a_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)
    with pytest.raises(ProviderError, match="OLLAMA_BASE_URL"):
        ollama_client_from_env()
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://localhost:11434/")
    assert str(ollama_client_from_env().base_url).rstrip("/") == "http://localhost:11434/v1"


def test_ollama_errors_name_the_provider_not_the_model() -> None:
    error = openai.APIConnectionError(message=f"cannot reach {DEPLOYMENT}", request=_REQ)
    adapter = OllamaAdapter(openai_client(Recorder(error=error)))
    with pytest.raises(ProviderError) as caught:
        asyncio.run(adapter.complete(request(), DEPLOYMENT))
    assert str(caught.value) == "ollama call failed: APIConnectionError"


# Anthropic ---------------------------------------------------------------------------------


def test_anthropic_moves_system_messages_to_the_system_field() -> None:
    create = Recorder(anthropic_result('{"ok": true}'))
    response = asyncio.run(
        AnthropicAdapter(anthropic_client(create)).complete(request(), DEPLOYMENT)
    )
    assert create.kwargs["model"] == DEPLOYMENT
    assert create.kwargs["messages"] == [{"role": "user", "content": "Draft pkg-04."}]
    assert create.kwargs["system"].startswith("You draft credit memos.")
    assert "single JSON object" in create.kwargs["system"]
    # Thinking counts against max_tokens, so the budget gets headroom.
    assert create.kwargs["max_tokens"] == ANTHROPIC_MIN_MAX_TOKENS
    assert "temperature" not in create.kwargs
    assert (response.text, response.input_tokens, response.output_tokens) == ('{"ok": true}', 13, 5)


def test_anthropic_strips_code_fences_from_json_answers() -> None:
    create = Recorder(anthropic_result('```json\n{"ok": true}\n```'))
    response = asyncio.run(
        AnthropicAdapter(anthropic_client(create)).complete(request(), DEPLOYMENT)
    )
    assert response.text == '{"ok": true}'


def test_anthropic_leaves_plain_text_alone() -> None:
    create = Recorder(anthropic_result("```code```"))
    response = asyncio.run(
        AnthropicAdapter(anthropic_client(create)).complete(request(json_output=False), DEPLOYMENT)
    )
    assert response.text == "```code```" and "JSON" not in create.kwargs["system"]


@pytest.mark.parametrize(
    ("stop_reason", "why"), [("refusal", "declined"), ("max_tokens", "cut off")]
)
def test_anthropic_incomplete_answers_are_provider_errors(stop_reason: str, why: str) -> None:
    # A provider error lets the router fall back to the next model.
    create = Recorder(anthropic_result("{", stop_reason=stop_reason))
    with pytest.raises(ProviderError, match=why):
        asyncio.run(AnthropicAdapter(anthropic_client(create)).complete(request(), DEPLOYMENT))


@pytest.mark.parametrize(
    "error",
    [
        anthropic.APIConnectionError(message=f"cannot reach {DEPLOYMENT}", request=_REQ),
        anthropic.NotFoundError(
            f"model {DEPLOYMENT} not found",
            response=httpx.Response(404, request=_REQ),
            body=None,
        ),
    ],
    ids=["connection", "not_found"],
)
def test_anthropic_errors_never_carry_the_model_name(error: Exception) -> None:
    adapter = AnthropicAdapter(anthropic_client(Recorder(error=error)))
    with pytest.raises(ProviderError) as caught:
        asyncio.run(adapter.complete(request(), DEPLOYMENT))
    assert DEPLOYMENT not in str(caught.value)
    assert str(caught.value) == f"anthropic call failed: {type(error).__name__}"


def test_anthropic_client_needs_a_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(ProviderError, match="ANTHROPIC_API_KEY"):
        anthropic_client_from_env()
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://gateway.example.test")
    client = anthropic_client_from_env()
    assert str(client.base_url).startswith("https://gateway.example.test")


# The router with several providers ---------------------------------------------------------


def test_every_provider_has_an_adapter_and_unknown_ones_are_named() -> None:
    assert set(ADAPTERS) == {"openai", "anthropic", "ollama"}
    with pytest.raises(ProviderError, match="known: anthropic, ollama, openai"):
        _default_factory("bedrock")


def test_one_router_mixes_providers_by_alias(tmp_path: Any) -> None:
    clients = {
        "openai": OpenAIAdapter(openai_client(Recorder(openai_result('{"by": "openai"}')))),
        "anthropic": AnthropicAdapter(
            anthropic_client(Recorder(anthropic_result('{"by": "anthropic"}')))
        ),
    }
    policy = RoutingPolicy(
        routes={
            "narrative": {"primary": "frontier-b"},
            "extraction": {"primary": "small-a"},
        }
    )
    aliases = {
        "small-a": AliasTarget("openai", "dep-1"),
        "frontier-b": AliasTarget("anthropic", "dep-2"),
    }
    router = ModelRouter(policy, aliases, tmp_path, adapter_factory=clients.__getitem__)
    narrative, decision = asyncio.run(router.complete(request()))
    assert narrative.text == '{"by": "anthropic"}' and decision.provider == "anthropic"
    extraction, decision = asyncio.run(
        router.complete(request().model_copy(update={"route": "extraction"}))
    )
    assert extraction.text == '{"by": "openai"}' and decision.provider == "openai"
