"""Structured model calls every app's agents share: one validated retry, and every attempt
recorded for the evidence log, including rejected ones (ADR 0016 moved this from the credit
memo into the core)."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel, ValidationError

from axiom.core.prompts import Prompt
from axiom.core.types import ModelCall
from axiom.router.router import ModelRouter
from axiom.router.types import ChatMessage, ModelRequest, Route, Sensitivity

T = TypeVar("T", bound=BaseModel)


class AgentOutputError(RuntimeError):
    """A model's output failed validation twice. The job fails with this reason."""


class HasRouter(Protocol):
    @property
    def router(self) -> ModelRouter: ...


_CALL_SINK: ContextVar[list[ModelCall] | None] = ContextVar("axiom_model_calls", default=None)
_SENSITIVITY: ContextVar[Sensitivity] = ContextVar("axiom_sensitivity", default="pii_unredacted")


@contextmanager
def sensitivity(level: Sensitivity) -> Iterator[None]:
    """Mark every model call inside the block. Calls are pii_unredacted unless a node says
    the documents were redacted, so a missing redaction can never route as standard."""
    token = _SENSITIVITY.set(level)
    try:
        yield
    finally:
        _SENSITIVITY.reset(token)


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
    ctx: HasRouter,
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
            sensitivity=_SENSITIVITY.get(),
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
