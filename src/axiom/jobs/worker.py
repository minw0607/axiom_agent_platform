"""The worker loop: claim a job, run or resume the workflow, store the result.

A new job runs until it pauses for review. A reviewed job resumes from its checkpoint with
the credit officer's decision: approve finalizes it, revise redrafts and pauses again, and
reject closes it. Each segment of work appends its own evidence events.

uv run python -m axiom.jobs.worker
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
from typing import Any

from axiom.agents.base import PROMPT_VERSIONS, AgentContext
from axiom.catalog import UnknownPackage, load_package
from axiom.evidence.log import EventDraft
from axiom.graph.workflow import (
    MemoState,
    ReviewError,
    paused_for_review,
    resume_workflow,
    run_workflow,
)
from axiom.guardrails import load_suite
from axiom.jobs.store import JobRecord, JobStatus, JobStore, PostgresJobStore
from axiom.mcp_clients.client import MCPToolClient
from axiom.mcp_clients.evidence import EvidenceService, GKNEvidence, InMemoryEvidence
from axiom.mcp_clients.governance import RegulusGovernance
from axiom.router.router import ModelRouter
from axiom.settings import Settings
from axiom.telemetry.setup import setup_telemetry, shutdown_telemetry, tracer
from axiom.tools.policy import load_policy

log = logging.getLogger("axiom.worker")

# Public failure reasons. Internal detail stays in the stored result for auditors.
ERROR_CODES = {
    "IntakeError": "invalid_package",
    "AgentOutputError": "output_validation_failed",
    "ProviderError": "model_unavailable",
    "FailClosed": "no_permitted_model",
    "ReviewError": "review_not_applied",
}


def build_context(settings: Settings) -> AgentContext:
    evidence: EvidenceService
    if settings.gkn_mcp_url and settings.mcp_shared_secret:
        evidence = GKNEvidence(MCPToolClient(settings.gkn_mcp_url, settings.mcp_shared_secret))
    else:
        log.warning("GKN not configured; using in-memory evidence retrieval")
        evidence = InMemoryEvidence(settings.reference_dirs)
    return AgentContext(
        router=ModelRouter.from_settings(
            settings.config_dir, settings.mode, settings.recordings_dir
        ),
        evidence=evidence,
        policy=load_policy(settings.config_dir / "policy_thresholds.yaml"),
        prompts_dir=settings.prompts_dir,
        guardrails=load_suite(settings.guardrails_file),
        governance=(
            RegulusGovernance(MCPToolClient(settings.regulus_mcp_url, settings.mcp_shared_secret))
            if settings.regulus_mcp_url and settings.mcp_shared_secret
            else None
        ),
    )


def serialize_state(state: MemoState, policy_version: str) -> dict[str, Any]:
    def dump(value: Any) -> Any:
        if isinstance(value, list):
            return [dump(v) for v in value]
        if hasattr(value, "model_dump"):
            return value.model_dump(mode="json")
        return value

    keys = (
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
        "outcome",
        "related_guidance",
        "approved_draft_sha256",
        "model_calls",
        "completed",
        "error",
        "error_node",
    )
    out = {k: dump(state[k]) for k in keys if k in state}  # type: ignore[literal-required]
    out["policy_version"] = policy_version
    return out


# State keys that are each node's output, hashed into its node_completed event.
NODE_OUTPUTS = {
    "intake": ("source_hashes", "financials", "checks"),
    "analyst": ("ratios", "risk_flags", "narrative"),
    "drafter": ("draft", "evidence"),
    "compliance": ("findings", "reference_evidence"),
    "finalize": ("approved_draft_sha256", "related_guidance"),
}
WORKER = "worker"


def _sha256(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def run_events(
    result: dict[str, Any],
    calls_before: int = 0,
    completed_before: int = 0,
    guardrails_before: int = 0,
) -> list[EventDraft]:
    """The evidence events for one segment of a run, in order: for each node, its input
    guardrails, its model calls (every attempt, valid or not), its other guardrails, then
    the node's outcome. A resumed run passes how many calls, completed nodes and guardrail
    results the earlier segments already logged."""
    events: list[EventDraft] = []
    calls = result.get("model_calls", [])[calls_before:]
    checked = result.get("guardrails", [])[guardrails_before:]

    def guardrail_events(node: str, input_stage: bool) -> list[EventDraft]:
        return [
            EventDraft(type="guardrail_checked", actor=WORKER, payload=g)
            for g in checked
            if g["node"] == node and (g["stage"] == "input") == input_stage
        ]

    nodes = list(result.get("completed", [])[completed_before:])
    if result.get("error_node"):
        nodes.append(result["error_node"])
    for node in nodes:
        events += guardrail_events(node, input_stage=True)
        for call in (c for c in calls if c["node"] == node):
            events.append(EventDraft(type="model_call", actor=WORKER, payload=call))
        events += guardrail_events(node, input_stage=False)
        if node == result.get("error_node") and node not in result["completed"][completed_before:]:
            events.append(
                EventDraft(
                    type="node_failed",
                    actor=WORKER,
                    payload={"node": node, "error": result.get("error", "")[:500]},
                )
            )
            continue
        payload: dict[str, Any] = {
            "node": node,
            "output_sha256": _sha256({k: result.get(k) for k in NODE_OUTPUTS.get(node, ())}),
        }
        if node == "analyst":
            payload["risk_flags"] = [f["code"] for f in result.get("risk_flags", [])]
        if node == "drafter":
            payload["draft_recommendation"] = result["draft"]["draft_recommendation"]
        if node == "compliance":
            payload["triggered"] = [
                f["rule_id"] for f in result.get("findings", []) if f["outcome"] == "triggered"
            ]
            payload["not_evaluated"] = [
                f["rule_id"] for f in result.get("findings", []) if f["outcome"] == "not_evaluated"
            ]
        if node == "finalize":
            guidance = result.get("related_guidance") or {}
            payload["approved_draft_sha256"] = result.get("approved_draft_sha256")
            payload["related_guidance"] = {
                "status": guidance.get("status"),
                "provisions": [p["citation"] for p in guidance.get("provisions", [])],
            }
        events.append(EventDraft(type="node_completed", actor=WORKER, payload=payload))
    return events


async def process_job(
    store: JobStore, ctx: AgentContext, settings: Settings, job: JobRecord
) -> None:
    with tracer().start_as_current_span("job run") as span:
        span.set_attribute("axiom.job_id", job.job_id)
        trace = span.get_span_context().trace_id
        trace_id = f"{trace:032x}" if trace else None
        await store.append_events(
            job.job_id,
            [
                EventDraft(
                    type="job_started",
                    actor=WORKER,
                    payload={
                        "mode": settings.mode,
                        "policy_version": ctx.policy.version,
                        "prompt_versions": PROMPT_VERSIONS,
                        "trace_id": trace_id,
                    },
                )
            ],
        )
        try:
            package = load_package(settings.packages_dir, job.package_id)
        except UnknownPackage:
            await _fail(store, job.job_id, None, "invalid_package")
            return
        async with store.checkpointer() as saver:
            state = await run_workflow(ctx, job.job_id, package, saver)
        result = serialize_state(state, ctx.policy.version)
        result["trace_id"] = trace_id
        await store.append_events(job.job_id, run_events(result))
        status = await _settle(store, job.job_id, state, result)
        span.set_attribute("axiom.job.status", status)


async def resume_job(
    store: JobStore, ctx: AgentContext, settings: Settings, job: JobRecord
) -> None:
    """Resume a reviewed job from its checkpoint with the credit officer's decision."""
    review = job.pending_review
    if review is None:
        await _fail(store, job.job_id, job.result, "internal_error")
        return
    before = job.result or {}
    with tracer().start_as_current_span("job resume") as span:
        span.set_attribute("axiom.job_id", job.job_id)
        span.set_attribute("axiom.review.decision", review.decision)
        await store.append_events(
            job.job_id,
            [
                EventDraft(
                    type="job_resumed",
                    actor=WORKER,
                    payload={"round": review.round, "decision": review.decision},
                )
            ],
        )
        try:
            async with store.checkpointer() as saver:
                state = await resume_workflow(ctx, job.job_id, review, saver)
        except ReviewError:
            await _fail(store, job.job_id, before, "review_not_applied")
            return
        result = serialize_state(state, ctx.policy.version)
        result["trace_id"] = before.get("trace_id")
        await store.append_events(
            job.job_id,
            run_events(
                result,
                len(before.get("model_calls", [])),
                len(before.get("completed", [])),
                len(before.get("guardrails", [])),
            ),
        )
        status = await _settle(store, job.job_id, state, result)
        span.set_attribute("axiom.job.status", status)


async def _settle(
    store: JobStore, job_id: str, state: MemoState, result: dict[str, Any]
) -> JobStatus:
    """Record how a segment ended: paused for review, approved, rejected or failed."""
    if state.get("error"):
        error_type = state["error"].split(":", 1)[0]
        await _fail(store, job_id, result, ERROR_CODES.get(error_type, "internal_error"))
        return "failed"
    status: JobStatus
    if paused_for_review(state):
        status, event = "awaiting_review", "job_awaiting_review"
    elif state.get("outcome") == "approved":
        status, event = "approved", "job_approved"
    elif state.get("outcome") == "rejected":
        status, event = "rejected", "job_rejected"
    else:
        await _fail(store, job_id, result, "internal_error")
        return "failed"
    payload: dict[str, Any] = {"result_sha256": _sha256(result)}
    if status != "awaiting_review":
        last = result["reviews"][-1]
        payload |= {"reviewer": last["reviewer"], "round": last["round"]}
    await store.append_events(job_id, [EventDraft(type=event, actor=WORKER, payload=payload)])  # type: ignore[arg-type]
    await store.finish(job_id, status, result, None)
    return status


async def _fail(store: JobStore, job_id: str, result: dict[str, Any] | None, code: str) -> None:
    await store.append_events(
        job_id, [EventDraft(type="job_failed", actor=WORKER, payload={"error_code": code})]
    )
    await store.finish(job_id, "failed", result, code)


async def run_once(store: JobStore, ctx: AgentContext, settings: Settings) -> bool:
    job = await store.claim_next()
    if job is None:
        return False
    try:
        if job.pending_review is not None:
            await resume_job(store, ctx, settings, job)
        else:
            await process_job(store, ctx, settings, job)
    except Exception:
        log.exception("job %s failed unexpectedly", job.job_id)
        await _fail(store, job.job_id, None, "internal_error")
    return True


async def run_worker(
    store: JobStore, settings: Settings, stop: asyncio.Event, poll_seconds: float = 0.5
) -> None:
    ctx = build_context(settings)
    while not stop.is_set():
        if not await run_once(store, ctx, settings):
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=poll_seconds)


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    settings = Settings.from_env()
    if not settings.database_url:
        raise SystemExit("DATABASE_URL is required for the worker")
    setup_telemetry("axiom-worker", settings.otel_endpoint)
    store = PostgresJobStore(settings.database_url)

    async def run() -> None:
        await store.init()
        await run_worker(store, settings, asyncio.Event())

    try:
        asyncio.run(run())
    finally:
        shutdown_telemetry()


if __name__ == "__main__":
    main()
