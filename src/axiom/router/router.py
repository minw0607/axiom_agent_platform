"""The model router (ADR 0005).

Order of decisions: sensitivity rules, then the task route, then the fallback.
Aliases resolve to provider and deployment through MODEL_ALIAS_MAP in the environment,
so the repository and every published output name only aliases.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from axiom.router.adapters import OpenAIAdapter, RecordedAdapter, save_recording
from axiom.router.types import (
    FailClosed,
    ModelAdapter,
    ModelRequest,
    ModelResponse,
    ProviderError,
    RoutingDecision,
)
from axiom.telemetry.setup import tracer

RECORDED_ALIAS = "recorded"


@dataclass(frozen=True)
class AliasTarget:
    provider: str
    deployment: str


def parse_alias_map(raw: str) -> dict[str, AliasTarget]:
    """Parse 'alias=provider:deployment,alias2=provider:deployment'."""
    out: dict[str, AliasTarget] = {}
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        alias, sep, target = item.partition("=")
        provider, sep2, deployment = target.partition(":")
        if not (sep and sep2 and alias and provider and deployment):
            raise ValueError("MODEL_ALIAS_MAP entries must look like alias=provider:deployment")
        out[alias.strip()] = AliasTarget(provider.strip(), deployment.strip())
    return out


@dataclass(frozen=True)
class RoutingPolicy:
    routes: dict[str, dict[str, str]]
    sensitivity: dict[str, dict[str, object]] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> RoutingPolicy:
        raw = yaml.safe_load(path.read_text())
        return cls(routes=raw["routes"], sensitivity=raw.get("sensitivity") or {})


AdapterFactory = Callable[[str], ModelAdapter]


class ModelRouter:
    def __init__(
        self,
        policy: RoutingPolicy,
        aliases: dict[str, AliasTarget],
        recordings_dir: Path,
        adapter_factory: AdapterFactory | None = None,
        record: bool = False,
    ) -> None:
        self.policy = policy
        self.aliases = aliases
        self.recordings_dir = recordings_dir
        self.record = record
        self._recorded = RecordedAdapter(recordings_dir)
        self._factory = adapter_factory or _default_factory
        self._adapters: dict[str, ModelAdapter] = {}

    @classmethod
    def from_settings(cls, config_dir: Path, mode: str, recordings_dir: Path) -> ModelRouter:
        return cls(
            RoutingPolicy.load(config_dir / f"routing.{mode}.yaml"),
            parse_alias_map(os.environ.get("MODEL_ALIAS_MAP", "")),
            recordings_dir,
            record=os.environ.get("AXIOM_RECORD", "").lower() in {"1", "true", "yes"},
        )

    def _adapter(self, provider: str) -> ModelAdapter:
        if provider not in self._adapters:
            self._adapters[provider] = self._factory(provider)
        return self._adapters[provider]

    def _candidates(self, request: ModelRequest) -> tuple[list[str], str]:
        rule = self.policy.sensitivity.get(request.sensitivity)
        if rule is not None:
            allowed = [str(a) for a in rule.get("allow", [])]  # type: ignore[attr-defined]
            usable = [a for a in allowed if a == RECORDED_ALIAS or a in self.aliases]
            if not usable:
                raise FailClosed(
                    f"{request.sensitivity} content has no permitted model in this environment"
                )
            return usable, f"sensitivity rule {request.sensitivity}"
        route = self.policy.routes.get(request.route)
        if route is None:
            raise FailClosed(f"no route configured for {request.route}")
        order = [route["primary"]] + ([route["fallback"]] if route.get("fallback") else [])
        return order, f"route {request.route}"

    async def complete(self, request: ModelRequest) -> tuple[ModelResponse, RoutingDecision]:
        candidates, reason = self._candidates(request)
        errors: list[str] = []
        for index, alias in enumerate(candidates):
            adapter: ModelAdapter
            if alias == RECORDED_ALIAS:
                provider, deployment, adapter = "recorded", "", self._recorded
            elif alias in self.aliases:
                target = self.aliases[alias]
                provider, deployment = target.provider, target.deployment
                try:
                    adapter = self._adapter(provider)
                except ProviderError as exc:
                    errors.append(f"{alias}: {exc}")
                    continue
            else:
                errors.append(f"{alias}: alias not configured")
                continue
            with tracer().start_as_current_span(f"chat {alias}") as span:
                span.set_attribute("gen_ai.operation.name", "chat")
                span.set_attribute("gen_ai.request.model", alias)  # alias only, by design
                span.set_attribute("gen_ai.system", provider)
                span.set_attribute("axiom.route", request.route)
                span.set_attribute("axiom.prompt.id", request.prompt_id)
                span.set_attribute("axiom.prompt.version", request.prompt_version)
                try:
                    response = await adapter.complete(request, deployment)
                except ProviderError as exc:
                    span.set_attribute("error.type", "provider_error")
                    errors.append(f"{alias}: {exc}")
                    continue
                span.set_attribute("gen_ai.usage.input_tokens", response.input_tokens)
                span.set_attribute("gen_ai.usage.output_tokens", response.output_tokens)
                span.set_attribute("axiom.response.origin", response.origin)
            if self.record and response.origin == "live":
                save_recording(self.recordings_dir, request, response, origin="recorded")
            decision = RoutingDecision(
                route=request.route,
                alias=alias,
                provider=provider,
                reason=reason if index == 0 else f"{reason}; fallback after: {errors[-1]}",
                fallback_used=index > 0,
                sensitivity=request.sensitivity,
            )
            return response, decision
        raise ProviderError("all candidate models failed: " + "; ".join(errors))


def _default_factory(provider: str) -> ModelAdapter:
    if provider == "openai":
        return OpenAIAdapter()
    raise ProviderError(f"provider {provider} has no adapter yet")
