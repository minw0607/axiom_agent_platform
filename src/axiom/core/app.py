"""The contract between the platform core and an app (ADR 0016).

An app is a package under axiom.apps that exposes an `APP` object meeting this protocol and
is listed in config/apps.yaml. The core's worker, API and checkpoints reach app code only
through these members, so no core module imports an app.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel

if TYPE_CHECKING:
    from fastapi import APIRouter
    from langgraph.checkpoint.base import BaseCheckpointSaver

    from axiom.api.explain import HasDocuments
    from axiom.core.types import ReviewDecision
    from axiom.evidence.log import ChainCheck, EvidenceEvent
    from axiom.jobs.store import JobRecord
    from axiom.settings import Settings

Role = Literal["submitter", "reviewer", "auditor"]


@runtime_checkable
class App(Protocol):
    # Identity and display
    app_id: str
    title: str
    noun: str  # what a job produces, in API messages: "memo", "report"
    roles: Mapping[Role, str]  # what the core's three roles are called in this app

    # The review loop
    max_revisions: int
    # Exception class name -> public failure reason, added to the core's own codes.
    error_codes: Mapping[str, str]

    # Checkpoints: modules whose Pydantic models and enums may be in a paused job's state,
    # and the names those modules had before they moved ({old name: current module}).
    state_modules: tuple[ModuleType, ...]
    legacy_modules: Mapping[str, ModuleType]

    # Evidence: the state keys each node produces, hashed into its node_completed event.
    node_outputs: Mapping[str, tuple[str, ...]]

    # API: the request body, the public job and the evidence bundle; a route kept from
    # before ADR 0016 (or None); a description for the source route.
    request_model: type[BaseModel]
    job_model: type[BaseModel]
    evidence_model: type[BaseModel]
    legacy_route: str | None
    source_description: str

    def build_context(self, settings: Settings) -> Any:
        """What the workflow needs to run: router, tools, thresholds, prompts, guardrails."""

    def started(self, ctx: Any) -> dict[str, Any]:
        """Versions recorded in the job_started event (policy, prompts)."""

    def inputs(self, settings: Settings) -> list[dict[str, Any]]:
        """The catalog: one entry per input, each with a `package_id`."""

    def load_input(self, settings: Settings, input_id: str) -> HasDocuments:
        """Load one input. Raises UnknownInput."""

    def has_recordings(self, recordings_dir: Path, input_id: str) -> bool:
        """Whether demo mode can replay a run for this input."""

    async def run(
        self, ctx: Any, job_id: str, inp: Any, checkpointer: BaseCheckpointSaver[Any]
    ) -> Mapping[str, Any]:
        """Run a new job until it pauses for review, finishes or fails."""

    async def resume(
        self,
        ctx: Any,
        job_id: str,
        review: ReviewDecision,
        checkpointer: BaseCheckpointSaver[Any],
    ) -> Mapping[str, Any]:
        """Resume a reviewed job from its checkpoint. Raises ReviewError."""

    def paused_for_review(self, state: Mapping[str, Any]) -> bool: ...

    def serialize(self, state: Mapping[str, Any], ctx: Any) -> dict[str, Any]:
        """The stored result: the state as JSON."""

    def node_details(self, node: str, result: Mapping[str, Any]) -> dict[str, Any]:
        """Extra fields for a node's node_completed event."""

    def to_public(self, job: JobRecord) -> BaseModel: ...

    def to_evidence(
        self, job: JobRecord, events: list[EvidenceEvent], chain: ChainCheck
    ) -> BaseModel: ...

    def extra_routes(self, settings: Settings) -> APIRouter | None:
        """Routes outside the job routes, such as catalog or reference lookups."""
