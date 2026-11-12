"""Model router types: requests, responses, routing decisions and errors."""

from __future__ import annotations

from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict

Route = Literal["extraction", "narrative", "compliance", "judge"]  # judge: evaluation only
Sensitivity = Literal["standard", "pii_unredacted"]


class ChatMessage(BaseModel):
    model_config = ConfigDict(frozen=True)
    role: Literal["system", "user", "assistant"]
    content: str


class ModelRequest(BaseModel):
    """What an agent asks for. Agents name a route, never a model."""

    model_config = ConfigDict(frozen=True)
    route: Route
    messages: tuple[ChatMessage, ...]
    prompt_id: str
    prompt_version: str
    recording_key: str
    json_output: bool = True
    sensitivity: Sensitivity = "standard"
    max_output_tokens: int = 4000


class ModelResponse(BaseModel):
    model_config = ConfigDict(frozen=True)
    text: str
    input_tokens: int
    output_tokens: int
    latency_ms: int
    origin: Literal["live", "recorded", "scripted"]


class RoutingDecision(BaseModel):
    """Recorded in the evidence bundle. Names the logical alias, never a deployment."""

    model_config = ConfigDict(frozen=True)
    route: Route
    alias: str
    provider: str
    reason: str
    fallback_used: bool
    sensitivity: Sensitivity


class ProviderError(RuntimeError):
    """A provider call failed in a way a fallback may fix."""


class FailClosed(RuntimeError):
    """No permitted model can take this request. The job must stop."""


class ModelAdapter(Protocol):
    provider: str

    async def complete(self, request: ModelRequest, deployment: str) -> ModelResponse: ...
