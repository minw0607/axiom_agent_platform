"""The worker loop: claim a job, run or resume its app's workflow, store the result.

A new job runs until it pauses for review. A reviewed job resumes from its checkpoint with
the reviewer's decision: approve finalizes it, revise redrafts and pauses again, and
reject closes it. Each segment of work appends its own evidence events. The worker reaches
each app only through the registry (ADR 0016).

uv run python -m axiom.jobs.worker
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
from collections.abc import Mapping
from typing import Any

from axiom.core.app import App
from axiom.core.registry import enabled_apps, get_app
from axiom.core.types import ReviewError, UnknownInput
from axiom.evidence.log import EventDraft
from axiom.jobs.store import JobRecord, JobStatus, JobStore, PostgresJobStore
from axiom.settings import Settings
from axiom.telemetry.setup import setup_telemetry, shutdown_telemetry, tracer

log = logging.getLogger("axiom.worker")

# Public failure reasons. Internal detail stays in the stored result for auditors.
# Each app adds its own (App.error_codes).
ERROR_CODES = {
    "AgentOutputError": "output_validation_failed",
    "ProviderError": "model_unavailable",
    "FailClosed": "no_permitted_model",
    "ReviewError": "review_not_applied",
    "DocumentQuarantined": "document_quarantined",
    "GuardrailFailed": "guardrail_failed",
}


def error_codes(app: App) -> dict[str, str]:
    return ERROR_CODES | dict(app.error_codes)


def build_context(settings: Settings, app_id: str | None = None) -> Any:
    """The workflow context for one app: by default the first enabled app."""
    app = get_app(app_id) if app_id else next(iter(enabled_apps().values()))
    return app.build_context(settings)


WORKER = "worker"


def _sha256(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def run_events(
    result: dict[str, Any],
    calls_before: int = 0,
    completed_before: int = 0,
    guardrails_before: int = 0,
    tools_before: int = 0,
    app: App | None = None,
) -> list[EventDraft]:
    """The evidence events for one segment of a run, in order: for each node, its input
    guardrails, its model calls (every attempt, valid or not), its other guardrails, then
    the node's outcome. Tool calls through the gateway come after the input guardrails.
    A resumed run passes how many calls, completed nodes, guardrail results and tool calls
    the earlier segments already logged. The app adds its own fields to node_completed."""
    node_outputs = app.node_outputs if app else {}
    events: list[EventDraft] = []
    calls = result.get("model_calls", [])[calls_before:]
    checked = result.get("guardrails", [])[guardrails_before:]
    tools = result.get("tool_calls", [])[tools_before:]

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
        for tool in (t for t in tools if t["node"] == node):
            events.append(EventDraft(type="tool_call", actor=WORKER, payload=tool))
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
            "output_sha256": _sha256({k: result.get(k) for k in node_outputs.get(node, ())}),
        }
        if app is not None:
            payload |= app.node_details(node, result)
        events.append(EventDraft(type="node_completed", actor=WORKER, payload=payload))
    return events


async def process_job(store: JobStore, ctx: Any, settings: Settings, job: JobRecord) -> None:
    app = get_app(job.app_id)
    with tracer().start_as_current_span("job run") as span:
        span.set_attribute("axiom.job_id", job.job_id)
        span.set_attribute("axiom.app_id", job.app_id)
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
                        **app.started(ctx),
                        "trace_id": trace_id,
                    },
                )
            ],
        )
        try:
            inp = app.load_input(settings, job.package_id)
        except UnknownInput:
            await _fail(store, job.job_id, None, "invalid_package")
            return
        async with store.checkpointer() as saver:
            state = await app.run(ctx, job.job_id, inp, saver)
        result = app.serialize(state, ctx)
        result["trace_id"] = trace_id
        await store.append_events(job.job_id, run_events(result, app=app))
        status = await _settle(store, app, job.job_id, state, result)
        span.set_attribute("axiom.job.status", status)


async def resume_job(store: JobStore, ctx: Any, settings: Settings, job: JobRecord) -> None:
    """Resume a reviewed job from its checkpoint with the reviewer's decision."""
    app = get_app(job.app_id)
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
                state = await app.resume(ctx, job.job_id, review, saver)
        except ReviewError:
            await _fail(store, job.job_id, before, "review_not_applied")
            return
        result = app.serialize(state, ctx)
        result["trace_id"] = before.get("trace_id")
        await store.append_events(
            job.job_id,
            run_events(
                result,
                len(before.get("model_calls", [])),
                len(before.get("completed", [])),
                len(before.get("guardrails", [])),
                len(before.get("tool_calls", [])),
                app=app,
            ),
        )
        status = await _settle(store, app, job.job_id, state, result)
        span.set_attribute("axiom.job.status", status)


async def _settle(
    store: JobStore, app: App, job_id: str, state: Mapping[str, Any], result: dict[str, Any]
) -> JobStatus:
    """Record how a segment ended: paused for review, approved, rejected or failed."""
    if state.get("error"):
        error_type = state["error"].split(":", 1)[0]
        await _fail(store, job_id, result, error_codes(app).get(error_type, "internal_error"))
        return "failed"
    status: JobStatus
    if app.paused_for_review(state):
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


async def run_once(store: JobStore, ctx: Any, settings: Settings) -> bool:
    """Claim and work one job. `ctx` is either one app's context, used for every job (a
    single-app test), or a mapping of app ID to context."""
    job = await store.claim_next()
    if job is None:
        return False
    try:
        if isinstance(ctx, Mapping):
            ctx = ctx[job.app_id]
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
    ctx = {app_id: app.build_context(settings) for app_id, app in enabled_apps().items()}
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
