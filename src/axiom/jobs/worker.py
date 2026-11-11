"""The worker loop: claim a queued job, run the workflow, store the result.

uv run python -m axiom.jobs.worker
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any

from axiom.agents.base import AgentContext
from axiom.catalog import UnknownPackage, load_package
from axiom.graph.workflow import MemoState, run_workflow
from axiom.jobs.store import JobRecord, JobStore, PostgresJobStore
from axiom.mcp_clients.client import MCPToolClient
from axiom.mcp_clients.evidence import EvidenceService, GKNEvidence, InMemoryEvidence
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
}


def build_context(settings: Settings) -> AgentContext:
    evidence: EvidenceService
    if settings.gkn_mcp_url and settings.mcp_shared_secret:
        evidence = GKNEvidence(MCPToolClient(settings.gkn_mcp_url, settings.mcp_shared_secret))
    else:
        log.warning("GKN not configured; using in-memory evidence retrieval")
        evidence = InMemoryEvidence()
    return AgentContext(
        router=ModelRouter.from_settings(
            settings.config_dir, settings.mode, settings.recordings_dir
        ),
        evidence=evidence,
        policy=load_policy(settings.config_dir / "policy_thresholds.yaml"),
        prompts_dir=settings.prompts_dir,
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
        "evidence",
        "model_calls",
        "completed",
        "error",
        "error_node",
    )
    out = {k: dump(state[k]) for k in keys if k in state}  # type: ignore[literal-required]
    out["policy_version"] = policy_version
    return out


async def process_job(
    store: JobStore, ctx: AgentContext, settings: Settings, job: JobRecord
) -> None:
    with tracer().start_as_current_span("job run") as span:
        span.set_attribute("axiom.job_id", job.job_id)
        try:
            package = load_package(settings.packages_dir, job.package_id)
        except UnknownPackage:
            await store.finish(job.job_id, "failed", None, "invalid_package")
            return
        state = await run_workflow(ctx, job.job_id, package)
        result = serialize_state(state, ctx.policy.version)
        if state.get("error"):
            error_type = state["error"].split(":", 1)[0]
            code = ERROR_CODES.get(error_type, "internal_error")
            await store.finish(job.job_id, "failed", result, code)
            span.set_attribute("axiom.job.status", "failed")
        else:
            await store.finish(job.job_id, "awaiting_review", result, None)
            span.set_attribute("axiom.job.status", "awaiting_review")


async def run_once(store: JobStore, ctx: AgentContext, settings: Settings) -> bool:
    job = await store.claim_next()
    if job is None:
        return False
    try:
        await process_job(store, ctx, settings, job)
    except Exception:
        log.exception("job %s failed unexpectedly", job.job_id)
        await store.finish(job.job_id, "failed", None, "internal_error")
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
