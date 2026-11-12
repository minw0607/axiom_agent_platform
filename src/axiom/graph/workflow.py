"""The memo workflow as a LangGraph state graph (ADR 0001).

intake -> analyst -> drafter -> compliance -> human_review, then finalize, back to the
drafter, or the end. A rule-based supervisor, plain code with no model, picks each
transition: on to the next node, or to the end when a node fails.

human_review is a LangGraph interrupt: the run pauses there and the checkpointer keeps the
state, keyed by job_id, until a credit officer's decision resumes it. Approve goes to
finalize, revise goes back to the drafter with the comments (at most two human revisions),
and reject ends the run. No path reaches finalize without an approval.
"""

from __future__ import annotations

import hashlib
import operator
from collections.abc import Awaitable, Callable
from typing import Annotated, Any, TypedDict

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from opentelemetry.trace import Status, StatusCode

from axiom.agents.analyst import run_analyst
from axiom.agents.base import AgentContext, AgentOutputError, ModelCall, collect_calls
from axiom.agents.compliance import run_compliance_review
from axiom.agents.drafter import run_drafter
from axiom.agents.intake import IntakeError, run_intake
from axiom.domain import (
    MAX_HUMAN_REVISIONS,
    BorrowerPackage,
    CheckResult,
    ComplianceFinding,
    FinancialStatements,
    MemoDraft,
    RatioResult,
    ReviewDecision,
    RiskFlag,
)
from axiom.mcp_clients.evidence import EvidenceHit
from axiom.mcp_clients.governance import RelatedGuidance
from axiom.memory.checkpoint import serializer
from axiom.router.types import FailClosed, ProviderError
from axiom.telemetry.setup import tracer
from axiom.tools.policy_rules import RuleInput, evaluate
from axiom.tools.reconcile import reconcile

NODE_ORDER = ("intake", "analyst", "drafter", "compliance")
REVIEW_NODES = ("human_review", "finalize")
EXPECTED_ERRORS = (IntakeError, AgentOutputError, ProviderError, FailClosed)


class ReviewError(RuntimeError):
    """A review the graph cannot accept, such as a third revision. The API checks first."""


class MemoState(TypedDict, total=False):
    job_id: str
    package: BorrowerPackage
    source_hashes: dict[str, str]
    financials: FinancialStatements
    checks: list[CheckResult]
    ratios: list[RatioResult]
    risk_flags: list[RiskFlag]
    narrative: list[str]
    draft: MemoDraft
    findings: list[ComplianceFinding]
    evidence: list[EvidenceHit]
    reference_evidence: list[EvidenceHit]
    reviews: Annotated[list[ReviewDecision], operator.add]
    human_revisions: int
    automated_fixes: int  # guardrail fixes, counted apart from human revisions (week 5)
    outcome: str  # "approved" or "rejected", set once a credit officer decides
    approved_draft_sha256: str
    related_guidance: RelatedGuidance
    model_calls: Annotated[list[ModelCall], operator.add]
    completed: Annotated[list[str], operator.add]
    error: str
    error_node: str


Node = Callable[[MemoState], Awaitable[dict[str, Any]]]


def _traced(name: str, fn: Node) -> Node:
    async def run(state: MemoState) -> dict[str, Any]:
        with tracer().start_as_current_span(f"node {name}") as span:
            span.set_attribute("axiom.job_id", state["job_id"])
            span.set_attribute("axiom.node", name)
            with collect_calls() as calls:
                try:
                    update = await fn(state)
                except EXPECTED_ERRORS as exc:
                    span.set_status(Status(StatusCode.ERROR, type(exc).__name__))
                    # Keep the attempts that failed: the evidence log records every call.
                    return {
                        "error": f"{type(exc).__name__}: {exc}",
                        "error_node": name,
                        "model_calls": list(calls),
                    }
            span.set_attribute("axiom.node.model_calls", len(calls))
            return {**update, "model_calls": list(calls), "completed": [name]}

    return run


def build_workflow(ctx: AgentContext, checkpointer: BaseCheckpointSaver[Any] | None = None) -> Any:
    async def intake(state: MemoState) -> dict[str, Any]:
        package = state["package"]
        hashes = await ctx.evidence.ingest(state["job_id"], package.documents)
        fs, _ = await run_intake(ctx, package)
        return {"source_hashes": hashes, "financials": fs, "checks": reconcile(fs)}

    async def analyst(state: MemoState) -> dict[str, Any]:
        ratios, flags, narrative, _ = await run_analyst(
            ctx, state["package"], state["financials"], state["checks"]
        )
        return {"ratios": ratios, "risk_flags": flags, "narrative": narrative}

    async def drafter(state: MemoState) -> dict[str, Any]:
        reviews = state.get("reviews", [])
        revision = (state["draft"], reviews) if reviews else None
        draft, hits, _ = await run_drafter(
            ctx,
            state["job_id"],
            state["package"],
            state["financials"],
            state["ratios"],
            state["risk_flags"],
            state["checks"],
            state["narrative"],
            revision,
        )
        return {"draft": draft, "evidence": hits}

    async def compliance(state: MemoState) -> dict[str, Any]:
        rule_findings = evaluate(
            RuleInput(
                package=state["package"],
                financials=state["financials"],
                checks=state["checks"],
                flags=state["risk_flags"],
                draft=state["draft"],
                policy=ctx.policy,
            )
        )
        review, hits, _ = await run_compliance_review(
            ctx, state["job_id"], state["package"], state["draft"], state.get("human_revisions", 0)
        )
        return {"findings": rule_findings + review, "reference_evidence": hits}

    async def human_review(state: MemoState) -> dict[str, Any]:
        # Pauses the run. On resume, interrupt() returns the credit officer's decision.
        decision = ReviewDecision.model_validate(
            interrupt({"round": len(state.get("reviews", [])) + 1})
        )
        update: dict[str, Any] = {"reviews": [decision]}
        if decision.decision == "revise":
            done = state.get("human_revisions", 0)
            if done >= MAX_HUMAN_REVISIONS:
                raise ReviewError(f"at most {MAX_HUMAN_REVISIONS} human revisions")
            update["human_revisions"] = done + 1
        elif decision.decision == "reject":
            update["outcome"] = "rejected"
        return update

    async def finalize(state: MemoState) -> dict[str, Any]:
        last = state["reviews"][-1]
        if last.decision != "approve":  # unreachable by routing; a hard stop all the same
            raise ReviewError("finalize needs an approval")
        draft = state["draft"]
        return {
            "outcome": "approved",
            "approved_draft_sha256": hashlib.sha256(draft.model_dump_json().encode()).hexdigest(),
            "related_guidance": await related_guidance(ctx, state),
            "completed": ["finalize"],
        }

    def supervisor(after: str) -> Callable[[MemoState], str]:
        """Rule-based routing: stop on error, otherwise the next node in the fixed order,
        and after compliance, the credit officer's review."""

        def route(state: MemoState) -> str:
            if state.get("error"):
                return END
            i = NODE_ORDER.index(after)
            return NODE_ORDER[i + 1] if i + 1 < len(NODE_ORDER) else "human_review"

        return route

    def after_review(state: MemoState) -> str:
        decision = state["reviews"][-1].decision
        return {"approve": "finalize", "revise": "drafter"}.get(decision, END)

    graph = StateGraph(MemoState)
    nodes = (
        ("intake", intake),
        ("analyst", analyst),
        ("drafter", drafter),
        ("compliance", compliance),
    )
    for name, fn in nodes:
        graph.add_node(name, _traced(name, fn))  # type: ignore[call-overload]
    graph.add_edge(START, "intake")
    for name in NODE_ORDER:
        graph.add_conditional_edges(name, supervisor(name))
    # Not wrapped in _traced: interrupt() re-runs this node on resume, and it makes no calls.
    graph.add_node("human_review", human_review)
    graph.add_node("finalize", finalize)
    graph.add_conditional_edges("human_review", after_review)
    graph.add_edge("finalize", END)
    return graph.compile(checkpointer=checkpointer or InMemorySaver(serde=serializer()))


GOVERNANCE_DESCRIPTION = (
    "An AI system of specialist agents drafts commercial credit memos. Every number comes "
    "from a sourced figure or a tested ratio tool, every claim is cited, a rule engine and a "
    "reviewer agent check the draft against credit policy and lending references, a credit "
    "officer who did not submit the job approves it, and every model call and decision is "
    "logged in a hash-chained evidence record."
)


async def related_guidance(ctx: AgentContext, state: MemoState) -> RelatedGuidance:
    """Regulus provisions related to how this memo was produced. Never blocks finalize."""
    triggered = sorted({f.rule_id for f in state.get("findings", []) if f.outcome == "triggered"})
    query = (
        f"{GOVERNANCE_DESCRIPTION} Draft recommendation: {state['draft'].draft_recommendation}. "
        f"Human revisions: {state.get('human_revisions', 0)}. "
        f"Findings triggered: {', '.join(triggered) or 'none'}."
    )
    if ctx.governance is None:
        return RelatedGuidance(
            status="not_evaluated", reason="Regulus is not configured", query=query
        )
    try:
        provisions = await ctx.governance.lookup(query, k=5)
    except Exception as exc:  # related guidance must never stop an approved memo
        return RelatedGuidance(
            status="not_evaluated", reason=f"lookup failed ({type(exc).__name__})", query=query
        )
    return RelatedGuidance(status="attached", query=query, provisions=provisions)


def thread(job_id: str) -> dict[str, Any]:
    return {"configurable": {"thread_id": job_id}}


async def run_workflow(
    ctx: AgentContext,
    job_id: str,
    package: BorrowerPackage,
    checkpointer: BaseCheckpointSaver[Any] | None = None,
) -> MemoState:
    """Run a new job until it pauses for review, finishes, or fails."""
    workflow = build_workflow(ctx, checkpointer)
    with tracer().start_as_current_span("workflow memo") as span:
        span.set_attribute("axiom.job_id", job_id)
        span.set_attribute("axiom.package_id", package.package_id)
        state: MemoState = await workflow.ainvoke(
            {
                "job_id": job_id,
                "package": package,
                "model_calls": [],
                "completed": [],
                "reviews": [],
                "human_revisions": 0,
                "automated_fixes": 0,
            },
            thread(job_id),
        )
        if state.get("error"):
            span.set_status(Status(StatusCode.ERROR, state.get("error_node", "")))
        return state


async def resume_workflow(
    ctx: AgentContext,
    job_id: str,
    review: ReviewDecision,
    checkpointer: BaseCheckpointSaver[Any],
) -> MemoState:
    """Resume a paused job from its checkpoint with the credit officer's decision."""
    workflow = build_workflow(ctx, checkpointer)
    with tracer().start_as_current_span("workflow resume") as span:
        span.set_attribute("axiom.job_id", job_id)
        span.set_attribute("axiom.review.decision", review.decision)
        snapshot = await workflow.aget_state(thread(job_id))
        if snapshot.next != ("human_review",):
            raise ReviewError(f"job {job_id} is not paused for review")
        state: MemoState = await workflow.ainvoke(
            Command(resume=review.model_dump(mode="json")), thread(job_id)
        )
        if state.get("error"):
            span.set_status(Status(StatusCode.ERROR, state.get("error_node", "")))
        return state


def paused_for_review(state: MemoState) -> bool:
    return "__interrupt__" in state
