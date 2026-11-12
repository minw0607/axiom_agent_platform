"""Shared agent plumbing: structured model calls with one validated retry."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from axiom.agents.prompts import Prompt, load_prompt
from axiom.guardrails import GuardrailSuite, load_suite
from axiom.mcp_clients.evidence import EvidenceService
from axiom.mcp_clients.governance import GovernanceLookup
from axiom.router.router import ModelRouter
from axiom.router.types import ChatMessage, ModelRequest, Route
from axiom.settings import Settings
from axiom.tools.policy import PolicyThresholds

T = TypeVar("T", bound=BaseModel)

PROMPT_VERSIONS = {
    "intake": "v2",
    "analyst": "v1",
    "drafter": "v3",
    "drafter_revision": "v1",
    "compliance": "v2",
}


class AgentOutputError(RuntimeError):
    """A model's output failed validation twice. The job fails with this reason."""


@dataclass
class AgentContext:
    router: ModelRouter
    evidence: EvidenceService
    policy: PolicyThresholds
    prompts_dir: Path
    governance: GovernanceLookup | None = None
    guardrails: GuardrailSuite = field(
        default_factory=lambda: load_suite(Settings.from_env().guardrails_file)
    )

    def prompt(self, prompt_id: str) -> Prompt:
        return load_prompt(self.prompts_dir, prompt_id, PROMPT_VERSIONS[prompt_id])


class ModelCall(BaseModel):
    """One model call, for the evidence bundle. Never returned by the public API."""

    node: str
    route: str
    alias: str
    provider: str
    prompt_id: str
    prompt_version: str
    prompt_sha256: str
    origin: str
    attempt: int
    fallback_used: bool
    input_tokens: int
    output_tokens: int
    latency_ms: int
    valid: bool


_CALL_SINK: ContextVar[list[ModelCall] | None] = ContextVar("axiom_model_calls", default=None)


@contextmanager
def collect_calls() -> Iterator[list[ModelCall]]:
    """Collect every model call made inside the block, including attempts of a node that fails.

    The workflow wraps each node in this, so the evidence log sees rejected attempts too.
    """
    sink: list[ModelCall] = []
    token = _CALL_SINK.set(sink)
    try:
        yield sink
    finally:
        _CALL_SINK.reset(token)


async def call_structured(
    ctx: AgentContext,
    *,
    node: str,
    route: Route,
    prompt: Prompt,
    payload: dict[str, Any],
    schema: type[T],
    recording_key: str,
    validate: Callable[[T], list[str]] | None = None,
) -> tuple[T, list[ModelCall]]:
    """Call the routed model; on invalid output, retry once with the errors, then fail."""
    messages = [
        ChatMessage(role="system", content=prompt.text),
        ChatMessage(role="user", content=json.dumps(payload, indent=1, default=str)),
    ]
    calls: list[ModelCall] = []
    errors: list[str] = []
    for attempt in (1, 2):
        request = ModelRequest(
            route=route,
            messages=tuple(messages),
            prompt_id=prompt.prompt_id,
            prompt_version=prompt.version,
            recording_key=recording_key if attempt == 1 else f"{recording_key}.retry",
        )
        response, decision = await ctx.router.complete(request)
        errors = []
        parsed: T | None = None
        try:
            parsed = schema.model_validate_json(response.text)
        except ValidationError as exc:
            errors = [f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()][:20]
        if parsed is not None and validate is not None:
            errors = validate(parsed)
        call = ModelCall(
            node=node,
            route=route,
            alias=decision.alias,
            provider=decision.provider,
            prompt_id=prompt.prompt_id,
            prompt_version=prompt.version,
            prompt_sha256=prompt.sha256,
            origin=response.origin,
            attempt=attempt,
            fallback_used=decision.fallback_used,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            latency_ms=response.latency_ms,
            valid=not errors,
        )
        calls.append(call)
        if (sink := _CALL_SINK.get()) is not None:
            sink.append(call)
        if parsed is not None and not errors:
            return parsed, calls
        messages += [
            ChatMessage(role="assistant", content=response.text),
            ChatMessage(
                role="user",
                content="The output was invalid:\n- "
                + "\n- ".join(errors)
                + "\nReturn corrected JSON only.",
            ),
        ]
    raise AgentOutputError(f"{node} output invalid after retry: {'; '.join(errors[:5])}")
