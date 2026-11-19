"""The credit memo app's side of the contract with the core (ADR 0016)."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from types import ModuleType
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from langgraph.checkpoint.base import BaseCheckpointSaver

from axiom.api.auth import Principal, require
from axiom.api.errors import Problem, problems
from axiom.apps.credit_memo import domain
from axiom.apps.credit_memo.agents.base import PROMPT_VERSIONS, AgentContext
from axiom.apps.credit_memo.catalog import has_recordings, list_packages, load_package
from axiom.apps.credit_memo.explain import PolicyClause, policy_clause
from axiom.apps.credit_memo.graph import (
    MemoState,
    paused_for_review,
    resume_workflow,
    run_workflow,
)
from axiom.apps.credit_memo.guardrails import suite
from axiom.apps.credit_memo.schemas import (
    EvidenceBundle,
    MemoJob,
    MemoRequest,
    PackageOut,
    to_evidence,
    to_public,
)
from axiom.apps.credit_memo.tools.policy import load_policy
from axiom.core.app import Role
from axiom.core.types import MAX_HUMAN_REVISIONS, ReviewDecision
from axiom.evidence.log import ChainCheck, EvidenceEvent
from axiom.jobs.store import JobRecord
from axiom.mcp_clients.client import GatewayClient, MCPToolClient
from axiom.mcp_clients.evidence import EvidenceService, GKNEvidence, InMemoryEvidence
from axiom.mcp_clients.governance import RegulusGovernance
from axiom.router.router import ModelRouter
from axiom.settings import Settings

log = logging.getLogger("axiom.worker")
APP_ID = "credit_memo"

# The state saved as the job's result, in this order.
RESULT_KEYS = (
    "source_hashes",
    "financials",
    "checks",
    "ratios",
    "risk_flags",
    "narrative",
    "draft",
    "findings",
    "evidence",
    "reference_evidence",
    "reviews",
    "human_revisions",
    "automated_fixes",
    "guardrails",
    "pii_redacted",
    "outcome",
    "related_guidance",
    "approved_draft_sha256",
    "model_calls",
    "tool_calls",
    "completed",
    "error",
    "error_node",
)


def build_context(settings: Settings) -> AgentContext:
    """Tools go through the tool gateway when it is configured (the Docker stack), else
    straight to the services with the shared secret (local development), else in memory."""
    evidence: EvidenceService
    governance: RegulusGovernance | None = None
    if settings.tool_gateway_url and settings.tool_gateway_tokens:
        gateway = GatewayClient(settings.tool_gateway_url, settings.tool_gateway_tokens)
        evidence = GKNEvidence(gateway)
        governance = RegulusGovernance(gateway)
    elif settings.gkn_mcp_url and settings.mcp_shared_secret:
        evidence = GKNEvidence(MCPToolClient(settings.gkn_mcp_url, settings.mcp_shared_secret))
    else:
        log.warning("GKN not configured; using in-memory evidence retrieval")
        evidence = InMemoryEvidence(settings.reference_dirs)
    return AgentContext(
        router=ModelRouter.from_settings(
            settings.config_dir, settings.mode, settings.app_recordings_dir(APP_ID)
        ),
        evidence=evidence,
        policy=load_policy(settings.app_config_dir(APP_ID) / "policy_thresholds.yaml"),
        prompts_dir=settings.app_prompts_dir(APP_ID),
        guardrails=suite(settings.guardrails_file),
        governance=governance
        or (
            RegulusGovernance(MCPToolClient(settings.regulus_mcp_url, settings.mcp_shared_secret))
            if settings.regulus_mcp_url and settings.mcp_shared_secret
            else None
        ),
    )


def serialize_state(state: Mapping[str, Any], policy_version: str) -> dict[str, Any]:
    def dump(value: Any) -> Any:
        if isinstance(value, list):
            return [dump(v) for v in value]
        if hasattr(value, "model_dump"):
            return value.model_dump(mode="json")
        return value

    out = {k: dump(state[k]) for k in RESULT_KEYS if k in state}
    out["policy_version"] = policy_version
    return out


def node_details(node: str, result: Mapping[str, Any]) -> dict[str, Any]:
    if node == "analyst":
        return {"risk_flags": [f["code"] for f in result.get("risk_flags", [])]}
    if node == "drafter":
        return {"draft_recommendation": result["draft"]["draft_recommendation"]}
    if node == "compliance":
        findings = result.get("findings", [])
        return {
            "triggered": [f["rule_id"] for f in findings if f["outcome"] == "triggered"],
            "not_evaluated": [f["rule_id"] for f in findings if f["outcome"] == "not_evaluated"],
        }
    if node == "finalize":
        guidance = result.get("related_guidance") or {}
        return {
            "approved_draft_sha256": result.get("approved_draft_sha256"),
            "related_guidance": {
                "status": guidance.get("status"),
                "provisions": [p["citation"] for p in guidance.get("provisions", [])],
            },
        }
    return {}


class CreditMemoApp:
    app_id = APP_ID
    title = "Credit memo"
    noun = "memo"
    max_revisions = MAX_HUMAN_REVISIONS
    state_modules = (domain,)
    request_model = MemoRequest
    job_model = MemoJob
    evidence_model = EvidenceBundle
    legacy_route = "/v1/memos"
    source_description = (
        "Resolves a citation in this memo: a line of a borrower document, or a "
        "passage the job retrieved. Says whether the text matches the fingerprint recorded "
        "during the job. No model is called."
    )

    def __init__(self) -> None:
        self.roles: Mapping[Role, str] = {
            "submitter": "Relationship manager",
            "reviewer": "Credit officer",
            "auditor": "Auditor",
        }
        self.error_codes: Mapping[str, str] = {"IntakeError": "invalid_package"}
        # Before ADR 0016 the domain types lived in axiom.domain.
        self.legacy_modules: Mapping[str, ModuleType] = {"axiom.domain": domain}
        self.node_outputs: Mapping[str, tuple[str, ...]] = {
            "intake": ("source_hashes", "financials", "checks"),
            "analyst": ("ratios", "risk_flags", "narrative"),
            "drafter": ("draft", "evidence"),
            "compliance": ("findings", "reference_evidence"),
            "finalize": ("approved_draft_sha256", "related_guidance"),
        }

    def build_context(self, settings: Settings) -> AgentContext:
        return build_context(settings)

    def started(self, ctx: AgentContext) -> dict[str, Any]:
        return {"policy_version": ctx.policy.version, "prompt_versions": PROMPT_VERSIONS}

    def inputs(self, settings: Settings) -> list[dict[str, Any]]:
        return list_packages(settings.packages_dir)

    def load_input(self, settings: Settings, input_id: str) -> domain.BorrowerPackage:
        return load_package(settings.packages_dir, input_id)

    def has_recordings(self, settings: Settings, input_id: str) -> bool:
        return has_recordings(settings.app_recordings_dir(APP_ID), input_id)

    async def run(
        self,
        ctx: AgentContext,
        job_id: str,
        inp: domain.BorrowerPackage,
        checkpointer: BaseCheckpointSaver[Any],
    ) -> MemoState:
        return await run_workflow(ctx, job_id, inp, checkpointer)

    async def resume(
        self,
        ctx: AgentContext,
        job_id: str,
        review: ReviewDecision,
        checkpointer: BaseCheckpointSaver[Any],
    ) -> MemoState:
        return await resume_workflow(ctx, job_id, review, checkpointer)

    def paused_for_review(self, state: Mapping[str, Any]) -> bool:
        return paused_for_review(state)  # type: ignore[arg-type]

    def serialize(self, state: Mapping[str, Any], ctx: AgentContext) -> dict[str, Any]:
        return serialize_state(state, ctx.policy.version)

    def node_details(self, node: str, result: Mapping[str, Any]) -> dict[str, Any]:
        return node_details(node, result)

    def to_public(self, job: JobRecord) -> MemoJob:
        return to_public(job)

    def to_evidence(
        self, job: JobRecord, events: list[EvidenceEvent], chain: ChainCheck
    ) -> EvidenceBundle:
        return to_evidence(job, events, chain)

    def extra_routes(self, settings: Settings) -> APIRouter:
        router = APIRouter()

        @router.get(
            "/v1/packages",
            response_model=list[PackageOut],
            tags=["memos"],
            summary="The synthetic borrower packages a memo can be requested for",
            responses=problems(401),
        )
        async def packages(
            principal: Annotated[Principal, Depends(require("submitter", "reviewer", "auditor"))],
        ) -> list[PackageOut]:
            return [
                PackageOut.model_validate(p)
                for p in list_packages(settings.packages_dir)
                if settings.mode != "demo" or self.has_recordings(settings, p["package_id"])
            ]

        @router.get(
            "/v1/policy/{ref}",
            response_model=PolicyClause,
            tags=["memos"],
            summary="A credit policy clause or section, by reference (CP-4.1, CP-3)",
            responses=problems(401, 404),
        )
        async def get_policy(
            ref: str,
            principal: Annotated[Principal, Depends(require("submitter", "reviewer", "auditor"))],
        ) -> PolicyClause:
            clause = policy_clause(settings.reference_dirs["credit_policy"], ref)
            if clause is None:
                raise Problem(
                    404,
                    "policy-clause-not-found",
                    "No such clause in the credit policy",
                    "References outside the bank's credit policy, such as regulations, are "
                    "cited by the passages the finding lists.",
                )
            return clause

        return router


APP = CreditMemoApp()
