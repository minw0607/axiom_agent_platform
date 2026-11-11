"""Shared fixtures: a demo-mode agent context and a scripted fake model."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import pytest

from axiom.agents.base import AgentContext
from axiom.mcp_clients.evidence import InMemoryEvidence
from axiom.router.router import AliasTarget, ModelRouter, RoutingPolicy
from axiom.router.types import ModelAdapter, ModelRequest, ModelResponse
from axiom.settings import Settings
from axiom.tools.policy import load_policy

# Tests replay scripted recordings (built from ground truth), so their results never depend on
# a model's judgment. The demo replays the live recordings in data/recorded/.
SCRIPTED_RECORDINGS = Path(__file__).parent / "fixtures" / "recorded"
SETTINGS = replace(Settings.from_env(), recordings_override=SCRIPTED_RECORDINGS)


def demo_context() -> AgentContext:
    router = ModelRouter.from_settings(SETTINGS.config_dir, "demo", SETTINGS.recordings_dir)
    return AgentContext(router, InMemoryEvidence(), load_policy(), SETTINGS.prompts_dir)


@pytest.fixture
def ctx() -> AgentContext:
    return demo_context()


Responder = Callable[[ModelRequest], str]


class FakeModel:
    """Answers each request with responder(request); records every request it sees."""

    provider = "fake"

    def __init__(self, responder: Responder) -> None:
        self.responder = responder
        self.requests: list[ModelRequest] = []

    async def complete(self, request: ModelRequest, deployment: str) -> ModelResponse:
        self.requests.append(request)
        return ModelResponse(
            text=self.responder(request),
            input_tokens=1,
            output_tokens=1,
            latency_ms=0,
            origin="live",
        )


def fake_context(responder: Responder) -> tuple[AgentContext, FakeModel]:
    """`responder` answers each request, or returns '' to replay the recording.

    Retries replay the original recording, since retries have none of their own.
    """
    model = FakeModel(responder)
    recorded = demo_context().router._recorded

    class Combined:
        provider = "fake"

        async def complete(self, request: ModelRequest, deployment: str) -> ModelResponse:
            text = responder(request)
            if text:
                model.requests.append(request)
                return ModelResponse(
                    text=text, input_tokens=1, output_tokens=1, latency_ms=0, origin="live"
                )
            key = request.recording_key.removesuffix(".retry")
            return await recorded.complete(request.model_copy(update={"recording_key": key}))

    def factory(provider: str) -> ModelAdapter:
        return Combined()

    policy = RoutingPolicy(routes={r: {"primary": "fake"} for r in ("extraction", "narrative")})
    router = ModelRouter(
        policy, {"fake": AliasTarget("fake", "-")}, SETTINGS.recordings_dir, adapter_factory=factory
    )
    return AgentContext(router, InMemoryEvidence(), load_policy(), SETTINGS.prompts_dir), model
