"""The generic records every app and every core package share (ADR 0016).

An app extends these with its own domain types; the core never needs to know them.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field


class Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Document(Frozen):
    """A source document, addressed by line. An app restricts `kind` to its own kinds."""

    doc_id: str
    kind: str
    title: str
    text: str


class Citation(Frozen):
    doc_id: str
    locator: str = Field(description="'line N' for a source document, or a GKN chunk id")


class Claim(Frozen):
    text: str
    citations: tuple[Citation, ...] = ()


class CheckResult(Frozen):
    """Outcome of a deterministic check. A check that cannot run is not_evaluated."""

    name: str
    outcome: Literal["passed", "triggered", "not_evaluated"]
    detail: str
    year: int | None = None


# How many times a reviewer may send a draft back. An app may set its own (ADR 0016).
MAX_HUMAN_REVISIONS = 2


class ReviewDecision(Frozen):
    """A reviewer's decision on a draft. The reviewer comes from the authenticated key."""

    decision: Literal["approve", "reject", "revise"]
    comments: str = Field(default="", max_length=2000)
    reviewer: str
    round: int = Field(ge=1)


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


class DraftSection(Protocol):
    """A titled group of claims. The output guardrails read drafts through this shape."""

    @property
    def heading(self) -> str: ...
    @property
    def claims(self) -> Sequence[Claim]: ...


class Draft(Protocol):
    """Any app's draft result: sections of cited claims."""

    @property
    def sections(self) -> Sequence[DraftSection]: ...
