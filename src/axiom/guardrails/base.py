"""The shared guardrail interface (ADR 0004).

Every control answers one question about one stage of a job and returns `passed`,
`triggered` or `not_evaluated`. The suite runs the controls for a stage, applies the on/off
switches from `config/guardrails.yaml`, and records each answer as a GuardrailResult with its
own span. A control that is switched off, cannot reach its service, or raises never reports a
pass: its result is `not_evaluated`, with the reason.

Results carry codes and counts only, never document or draft text, because they go into the
evidence log and the public API.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Literal, Protocol

from opentelemetry.trace import Status, StatusCode
from pydantic import BaseModel, ConfigDict, Field

from axiom.domain import Document, MemoDraft
from axiom.telemetry.setup import tracer

Stage = Literal["input", "generation", "output"]
Outcome = Literal["passed", "triggered", "not_evaluated"]
# What a control did about a finding. A quarantine stops the job before any model call.
Action = Literal["none", "redacted", "quarantined"]
STAGES: tuple[Stage, ...] = ("input", "generation", "output")


class GuardrailResult(BaseModel):
    """One control's answer for one stage of one job."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    control: str
    stage: Stage
    node: str
    outcome: Outcome
    enabled: bool
    reason: str = Field(max_length=300)
    action: Action = "none"
    details: dict[str, Any] = Field(default_factory=dict)


@dataclass(frozen=True)
class Verdict:
    """What a control returns. The suite turns it into a GuardrailResult.

    An input control that rewrites the documents, such as redaction, returns them in
    `documents`; the controls after it, and the rest of the job, see the rewritten ones.
    """

    outcome: Outcome
    reason: str
    details: dict[str, Any] = field(default_factory=dict)
    action: Action = "none"
    documents: tuple[Document, ...] | None = None


@dataclass(frozen=True)
class Subject:
    """What a stage hands its controls: the documents at input, the draft after it."""

    job_id: str
    node: str
    documents: tuple[Document, ...] = ()
    draft: MemoDraft | None = None
    borrower_name: str = ""


class Guardrail(Protocol):
    @property
    def name(self) -> str: ...
    @property
    def stage(self) -> Stage: ...
    @property
    def description(self) -> str: ...

    async def check(self, subject: Subject) -> Verdict: ...


class GuardrailConfigError(ValueError):
    """config/guardrails.yaml does not match the controls the platform has."""


SWITCHED_OFF = "switched off in config/guardrails.yaml"


class GuardrailSuite:
    """The platform's controls in a fixed order, with their on/off switches."""

    def __init__(self, controls: list[Guardrail], enabled: dict[str, bool]) -> None:
        names = [c.name for c in controls]
        if len(set(names)) != len(names):
            raise GuardrailConfigError("two controls share a name")
        unknown = sorted(set(enabled) - set(names))
        missing = [n for n in names if n not in enabled]
        if unknown:
            raise GuardrailConfigError(f"unknown controls in config: {', '.join(unknown)}")
        if missing:
            # Every control is switched explicitly, so nothing is on or off by accident.
            raise GuardrailConfigError(f"controls missing from config: {', '.join(missing)}")
        self.controls = controls
        self.enabled = dict(enabled)

    def for_stage(self, stage: Stage) -> list[Guardrail]:
        return [c for c in self.controls if c.stage == stage]

    async def run(self, stage: Stage, subject: Subject) -> list[GuardrailResult]:
        results, _ = await self.screen(stage, subject)
        return results

    async def screen(self, stage: Stage, subject: Subject) -> tuple[list[GuardrailResult], Subject]:
        """Run the stage's controls in order, each on the documents the one before it left."""
        results: list[GuardrailResult] = []
        for control in self.for_stage(stage):
            result, documents = await self._check(control, subject)
            results.append(result)
            if documents is not None:
                subject = replace(subject, documents=documents)
        return results, subject

    async def _check(
        self, control: Guardrail, subject: Subject
    ) -> tuple[GuardrailResult, tuple[Document, ...] | None]:
        enabled = self.enabled[control.name]
        with tracer().start_as_current_span(f"guardrail {control.name}") as span:
            span.set_attribute("axiom.job_id", subject.job_id)
            span.set_attribute("axiom.guardrail.control", control.name)
            span.set_attribute("axiom.guardrail.stage", control.stage)
            span.set_attribute("axiom.guardrail.enabled", enabled)
            if not enabled:
                verdict = Verdict("not_evaluated", SWITCHED_OFF)
            else:
                try:
                    verdict = await control.check(subject)
                except Exception as exc:  # a broken control must never read as a pass
                    span.set_status(Status(StatusCode.ERROR, type(exc).__name__))
                    verdict = Verdict("not_evaluated", f"check failed ({type(exc).__name__})")
            span.set_attribute("axiom.guardrail.outcome", verdict.outcome)
            span.set_attribute("axiom.guardrail.action", verdict.action)
            result = GuardrailResult(
                control=control.name,
                stage=control.stage,
                node=subject.node,
                outcome=verdict.outcome,
                enabled=enabled,
                reason=verdict.reason[:300],
                action=verdict.action,
                details=verdict.details,
            )
            return result, verdict.documents


def latest(results: list[GuardrailResult]) -> list[GuardrailResult]:
    """Each control's most recent result, in first-run order. A redraft reruns the
    generation and output controls; the reviewer sees the run behind the current draft."""
    last: dict[str, GuardrailResult] = {}
    for r in results:
        last[r.control] = r  # dicts keep first-insertion order
    return list(last.values())
