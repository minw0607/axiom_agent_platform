"""Model documentation review's side of the contract with the core (ADR 0016, app 2)."""

from __future__ import annotations

from collections.abc import Mapping
from types import ModuleType
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from langgraph.checkpoint.base import BaseCheckpointSaver

from axiom.api.auth import Principal, require
from axiom.api.errors import problems
from axiom.apps.model_review import domain
from axiom.apps.model_review.agents import PROMPT_VERSIONS, ReviewContext
from axiom.apps.model_review.catalog import list_packages, load_package, packages_dir
from axiom.apps.model_review.graph import (
    APP_ID,
    ReviewState,
    paused_for_review,
    resume_workflow,
    run_workflow,
)
from axiom.apps.model_review.guardrails import suite
from axiom.apps.model_review.schemas import (
    ChecklistOut,
    ModelPackageOut,
    ModelReviewRequest,
    ReviewEvidenceBundle,
    ReviewJob,
    to_evidence,
    to_public,
)
from axiom.apps.model_review.tools.checklist import load_checklist
from axiom.core.app import Role
from axiom.core.types import MAX_HUMAN_REVISIONS, ReviewDecision
from axiom.evidence.log import ChainCheck, EvidenceEvent
from axiom.jobs.store import JobRecord
from axiom.mcp_clients.client import GatewayClient, MCPToolClient
from axiom.mcp_clients.governance import RegulusGovernance
from axiom.router.router import ModelRouter
from axiom.settings import Settings

# The state saved as the job's result, in this order.
RESULT_KEYS = (
    "source_hashes",
    "checks",
    "completeness",
    "assessments",
    "documentation_status",
    "provisions",
    "provisions_status",
    "report",
    "reviews",
    "human_revisions",
    "automated_fixes",
    "guardrails",
    "pii_redacted",
    "outcome",
    "approved_report_sha256",
    "model_calls",
    "tool_calls",
    "completed",
    "error",
    "error_node",
)


def checklist_path(settings: Settings) -> Any:
    return settings.app_config_dir(APP_ID) / "checklist.yaml"


def build_context(settings: Settings) -> ReviewContext:
    """The governance library goes through the tool gateway when it is configured (the
    Docker stack), else straight to the service with the shared secret (local development),
    else it is left out and the provisions step records not_evaluated."""
    governance: RegulusGovernance | None = None
    if settings.tool_gateway_url and settings.tool_gateway_tokens:
        governance = RegulusGovernance(
            GatewayClient(settings.tool_gateway_url, settings.tool_gateway_tokens)
        )
    elif settings.regulus_mcp_url and settings.mcp_shared_secret:
        governance = RegulusGovernance(
            MCPToolClient(settings.regulus_mcp_url, settings.mcp_shared_secret)
        )
    return ReviewContext(
        router=ModelRouter.from_settings(
            settings.config_dir, settings.mode, settings.app_recordings_dir(APP_ID)
        ),
        checklist=load_checklist(checklist_path(settings)),
        prompts_dir=settings.app_prompts_dir(APP_ID),
        guardrails=suite(settings.guardrails_file),
        governance=governance,
    )


def serialize_state(state: Mapping[str, Any], ctx: ReviewContext) -> dict[str, Any]:
    def dump(value: Any) -> Any:
        if isinstance(value, list):
            return [dump(v) for v in value]
        if isinstance(value, dict):
            return {k: dump(v) for k, v in value.items()}
        if hasattr(value, "model_dump"):
            return value.model_dump(mode="json")
        return value

    out = {k: dump(state[k]) for k in RESULT_KEYS if k in state}
    out["checklist_version"] = ctx.checklist.version
    out["prompt_versions"] = dict(PROMPT_VERSIONS)
    return out


def node_details(node: str, result: Mapping[str, Any]) -> dict[str, Any]:
    if node == "intake":
        completeness = result.get("completeness") or {}
        return {
            "present": completeness.get("present"),
            "total": completeness.get("total"),
            "critical_gaps": completeness.get("critical_gaps", []),
        }
    if node == "assessor":
        return {
            "weak": [
                a["item_id"] for a in result.get("assessments", []) if a["adequacy"] == "weak"
            ],
            "documentation_status": result.get("documentation_status"),
        }
    if node == "provisions":
        return {"provisions_status": result.get("provisions_status")}
    if node == "finalize":
        return {"approved_report_sha256": result.get("approved_report_sha256")}
    return {}


def has_recordings(settings: Settings, package_id: str) -> bool:
    """Whether demo mode can replay a run for this package: its assessor was recorded."""
    recordings = settings.app_recordings_dir(APP_ID)
    return any(p.is_file() for p in recordings.glob(f"assessor/*/{package_id}.json"))


class ModelReviewApp:
    app_id = APP_ID
    title = "Model documentation review"
    noun = "report"
    max_revisions = MAX_HUMAN_REVISIONS
    state_modules = (domain,)
    request_model = ModelReviewRequest
    job_model = ReviewJob
    evidence_model = ReviewEvidenceBundle
    legacy_route = None
    source_description = (
        "Resolves a citation in this report: a line of a submitted document. Says whether the "
        "text matches the fingerprint recorded during the job. No model is called."
    )

    def __init__(self) -> None:
        self.roles: Mapping[Role, str] = {
            "submitter": "Model owner",
            "reviewer": "Model validator",
            "auditor": "Auditor",
        }
        self.error_codes: Mapping[str, str] = {}
        self.legacy_modules: Mapping[str, ModuleType] = {}
        self.node_outputs: Mapping[str, tuple[str, ...]] = {
            "intake": ("source_hashes", "checks", "completeness"),
            "assessor": ("assessments", "documentation_status"),
            "provisions": ("provisions", "provisions_status"),
            "drafter": ("report",),
            "finalize": ("approved_report_sha256",),
        }

    def build_context(self, settings: Settings) -> ReviewContext:
        return build_context(settings)

    def started(self, ctx: ReviewContext) -> dict[str, Any]:
        return {"checklist_version": ctx.checklist.version, "prompt_versions": PROMPT_VERSIONS}

    def inputs(self, settings: Settings) -> list[dict[str, Any]]:
        return list_packages(packages_dir(settings.data_dir))

    def load_input(self, settings: Settings, input_id: str) -> domain.DocumentationPackage:
        return load_package(packages_dir(settings.data_dir), input_id)

    def has_recordings(self, settings: Settings, input_id: str) -> bool:
        return has_recordings(settings, input_id)

    async def run(
        self,
        ctx: ReviewContext,
        job_id: str,
        inp: domain.DocumentationPackage,
        checkpointer: BaseCheckpointSaver[Any],
    ) -> ReviewState:
        return await run_workflow(ctx, job_id, inp, checkpointer)

    async def resume(
        self,
        ctx: ReviewContext,
        job_id: str,
        review: ReviewDecision,
        checkpointer: BaseCheckpointSaver[Any],
    ) -> ReviewState:
        return await resume_workflow(ctx, job_id, review, checkpointer)

    def paused_for_review(self, state: Mapping[str, Any]) -> bool:
        return paused_for_review(state)  # type: ignore[arg-type]

    def serialize(self, state: Mapping[str, Any], ctx: ReviewContext) -> dict[str, Any]:
        return serialize_state(state, ctx)

    def node_details(self, node: str, result: Mapping[str, Any]) -> dict[str, Any]:
        return node_details(node, result)

    def to_public(self, job: JobRecord) -> ReviewJob:
        return to_public(job)

    def to_evidence(
        self, job: JobRecord, events: list[EvidenceEvent], chain: ChainCheck
    ) -> ReviewEvidenceBundle:
        return to_evidence(job, events, chain)

    def extra_routes(self, settings: Settings) -> APIRouter:
        router = APIRouter(prefix=f"/v1/apps/{APP_ID}")
        tags: list[Any] = [self.title]

        @router.get(
            "/packages",
            response_model=list[ModelPackageOut],
            tags=tags,
            summary="The synthetic model documentation packages a review can be requested for",
            responses=problems(401),
        )
        async def packages(
            principal: Annotated[Principal, Depends(require("submitter", "reviewer", "auditor"))],
        ) -> list[ModelPackageOut]:
            return [
                ModelPackageOut.model_validate(p)
                for p in self.inputs(settings)
                if settings.mode != "demo" or self.has_recordings(settings, p["package_id"])
            ]

        @router.get(
            "/checklist",
            response_model=ChecklistOut,
            tags=tags,
            summary="The documentation checklist the review applies, with each item's provisions",
            responses=problems(401),
        )
        async def checklist(
            principal: Annotated[Principal, Depends(require("submitter", "reviewer", "auditor"))],
        ) -> ChecklistOut:
            return ChecklistOut.model_validate(
                load_checklist(checklist_path(settings)).model_dump(mode="json")
            )

        return router


APP = ModelReviewApp()
