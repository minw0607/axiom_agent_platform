"""Model router: sensitivity first, then route, then fallback; aliases only in decisions."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from axiom.router.adapters import RecordedAdapter, save_recording
from axiom.router.router import AliasTarget, ModelRouter, RoutingPolicy, parse_alias_map
from axiom.router.types import (
    ChatMessage,
    FailClosed,
    ModelAdapter,
    ModelRequest,
    ModelResponse,
    ProviderError,
)

POLICY = RoutingPolicy(
    routes={"extraction": {"primary": "small-a", "fallback": "frontier-a"}},
    sensitivity={"pii_unredacted": {"allow": ["local-small"]}},
)


def request(**overrides: object) -> ModelRequest:
    base: dict[str, object] = {
        "route": "extraction",
        "messages": (ChatMessage(role="user", content="extract"),),
        "prompt_id": "intake",
        "prompt_version": "v1",
        "recording_key": "intake/v1/test",
    }
    return ModelRequest(**{**base, **overrides})  # type: ignore[arg-type]


class FakeAdapter:
    def __init__(self, provider: str, fail_on: set[str]) -> None:
        self.provider = provider
        self.fail_on = fail_on
        self.calls: list[str] = []

    async def complete(self, request: ModelRequest, deployment: str) -> ModelResponse:
        self.calls.append(deployment)
        if deployment in self.fail_on:
            raise ProviderError("boom")
        return ModelResponse(
            text=f"from {deployment}", input_tokens=3, output_tokens=2, latency_ms=1, origin="live"
        )


def router(
    tmp_path: Path, fail_on: set[str] = frozenset(), record: bool = False
) -> tuple[ModelRouter, FakeAdapter]:
    fake = FakeAdapter("fakeprov", set(fail_on))

    def factory(provider: str) -> ModelAdapter:
        return fake

    aliases = {
        "small-a": AliasTarget("fakeprov", "deploy-small"),
        "frontier-a": AliasTarget("fakeprov", "deploy-frontier"),
    }
    return ModelRouter(POLICY, aliases, tmp_path, adapter_factory=factory, record=record), fake


def test_primary_route_is_used(tmp_path: Path) -> None:
    r, _fake = router(tmp_path)
    response, decision = asyncio.run(r.complete(request()))
    assert response.text == "from deploy-small"
    assert decision.alias == "small-a"
    assert not decision.fallback_used


def test_fallback_on_provider_error(tmp_path: Path) -> None:
    r, _fake = router(tmp_path, fail_on={"deploy-small"})
    response, decision = asyncio.run(r.complete(request()))
    assert response.text == "from deploy-frontier"
    assert decision.fallback_used
    assert decision.alias == "frontier-a"


def test_decision_never_names_a_deployment(tmp_path: Path) -> None:
    r, _ = router(tmp_path, fail_on={"deploy-small"})
    _, decision = asyncio.run(r.complete(request()))
    assert "deploy-" not in decision.model_dump_json()


def test_all_candidates_failing_raises(tmp_path: Path) -> None:
    r, _ = router(tmp_path, fail_on={"deploy-small", "deploy-frontier"})
    with pytest.raises(ProviderError):
        asyncio.run(r.complete(request()))


def test_unredacted_pii_fails_closed_without_a_local_model(tmp_path: Path) -> None:
    r, fake = router(tmp_path)
    with pytest.raises(FailClosed):
        asyncio.run(r.complete(request(sensitivity="pii_unredacted")))
    assert fake.calls == []  # never sent to a hosted model


def test_unredacted_pii_goes_only_to_the_permitted_model(tmp_path: Path) -> None:
    r, fake = router(tmp_path)
    r.aliases["local-small"] = AliasTarget("fakeprov", "deploy-local")
    _, decision = asyncio.run(r.complete(request(sensitivity="pii_unredacted")))
    assert fake.calls == ["deploy-local"]
    assert decision.reason == "sensitivity rule pii_unredacted"


def test_recording_mode_saves_live_responses_for_replay(tmp_path: Path) -> None:
    r, _ = router(tmp_path, record=True)
    live, _ = asyncio.run(r.complete(request()))
    replay = asyncio.run(RecordedAdapter(tmp_path).complete(request()))
    assert replay.text == live.text
    assert replay.origin == "recorded"


def test_recorded_adapter_reports_prompt_drift(tmp_path: Path) -> None:
    original = request()
    save_recording(
        tmp_path,
        original,
        ModelResponse(text="x", input_tokens=1, output_tokens=1, latency_ms=0, origin="live"),
        origin="recorded",
    )
    adapter = RecordedAdapter(tmp_path)
    asyncio.run(adapter.complete(original))
    assert adapter.last_prompt_matches is True
    changed = request(messages=(ChatMessage(role="user", content="different"),))
    asyncio.run(adapter.complete(changed))
    assert adapter.last_prompt_matches is False


def test_missing_recording_is_a_provider_error(tmp_path: Path) -> None:
    with pytest.raises(ProviderError):
        asyncio.run(RecordedAdapter(tmp_path).complete(request(recording_key="nope/none")))


@pytest.mark.parametrize("key", ["../escape", "/abs/path"])
def test_recording_keys_cannot_escape_the_folder(tmp_path: Path, key: str) -> None:
    with pytest.raises(ValueError):
        asyncio.run(RecordedAdapter(tmp_path).complete(request(recording_key=key)))


def test_alias_map_parsing() -> None:
    parsed = parse_alias_map("small-a=openai:dep-1, frontier-a=openai:dep-2")
    assert parsed["frontier-a"] == AliasTarget("openai", "dep-2")
    with pytest.raises(ValueError):
        parse_alias_map("broken")


# Client construction from the environment ---------------------------------------

_CLIENT_VARS = (
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "OPENAI_API_VERSION",
    "OPENAI_APIM_HEADER_NAME",
    "OPENAI_APIM_SUBSCRIPTION_KEY",
)


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    for name in _CLIENT_VARS:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def test_client_needs_a_key(clean_env: pytest.MonkeyPatch) -> None:
    from axiom.router.adapters import openai_client_from_env

    with pytest.raises(ProviderError):
        openai_client_from_env()


def test_plain_openai_client(clean_env: pytest.MonkeyPatch) -> None:
    from openai import AsyncAzureOpenAI, AsyncOpenAI

    from axiom.router.adapters import openai_client_from_env

    clean_env.setenv("OPENAI_API_KEY", "test-key")
    client = openai_client_from_env()
    assert isinstance(client, AsyncOpenAI) and not isinstance(client, AsyncAzureOpenAI)


def test_api_version_selects_azure_with_gateway_header(clean_env: pytest.MonkeyPatch) -> None:
    from openai import AsyncAzureOpenAI

    from axiom.router.adapters import openai_client_from_env

    clean_env.setenv("OPENAI_API_KEY", "test-key")
    clean_env.setenv("OPENAI_BASE_URL", "https://gateway.example.test")
    clean_env.setenv("OPENAI_API_VERSION", "2025-01-01")
    clean_env.setenv("OPENAI_APIM_HEADER_NAME", "X-Test-Subscription")
    clean_env.setenv("OPENAI_APIM_SUBSCRIPTION_KEY", "sub-key")
    client = openai_client_from_env()
    assert isinstance(client, AsyncAzureOpenAI)
    assert client.default_headers["X-Test-Subscription"] == "sub-key"


def test_azure_without_endpoint_fails_closed(clean_env: pytest.MonkeyPatch) -> None:
    from axiom.router.adapters import openai_client_from_env

    clean_env.setenv("OPENAI_API_KEY", "test-key")
    clean_env.setenv("OPENAI_API_VERSION", "2025-01-01")
    with pytest.raises(ProviderError):
        openai_client_from_env()
