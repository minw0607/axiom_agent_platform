"""The memo workflow as a LangGraph state graph (ADR 0001).

intake -> analyst -> drafter -> compliance -> human_review, then finalize, back to the
drafter, or the end. Guardrails (ADR 0004) run inside intake, on the documents, and inside
the drafter, on each draft. A rule-based supervisor, plain code with no model, picks each
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
from decimal import Decimal
from typing import Annotated, Any, TypedDict, cast

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command
from opentelemetry.trace import Status, StatusCode

from axiom.apps.credit_memo.agents.analyst import run_analyst
from axiom.apps.credit_memo.agents.base import (
    AgentContext,
    sensitivity,
)
from axiom.apps.credit_memo.agents.compliance import run_compliance_review
from axiom.apps.credit_memo.agents.drafter import run_drafter
from axiom.apps.credit_memo.agents.intake import IntakeError, run_intake
from axiom.apps.credit_memo.domain import (
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
from axiom.apps.credit_memo.guardrails import DECISION_MESSAGES
from axiom.apps.credit_memo.tools.policy import PolicyThresholds
from axiom.apps.credit_memo.tools.policy_rules import RuleInput, evaluate
from axiom.apps.credit_memo.tools.reconcile import reconcile
from axiom.core.types import ModelCall, ReviewError
from axiom.core.workflow import (
    CORE_EXPECTED_ERRORS,
    DocumentQuarantined,
    GuardrailFailed,
    after_review,
    fails,
    review_node,
    thread,
    traced,
)
from axiom.guardrails import GuardrailResult, Subject, pii_protected
from axiom.guardrails.base import DraftFacts, SourcedValue
from axiom.guardrails.output import problems
from axiom.mcp_clients.client import ToolCall, acting_as, collect_tool_calls
from axiom.mcp_clients.evidence import EvidenceHit
from axiom.mcp_clients.governance import RelatedGuidance
from axiom.memory.checkpoint import serializer
from axiom.telemetry.setup import tracer

APP_ID = "credit_memo"
NODE_ORDER = ("intake", "analyst", "drafter", "compliance")
REVIEW_NODES = ("human_review", "finalize")


EXPECTED_ERRORS: tuple[type[Exception], ...] = (IntakeError, *CORE_EXPECTED_ERRORS)


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
    guardrails: Annotated[list[GuardrailResult], operator.add]
    pii_redacted: bool  # set by intake; False routes every model call as pii_unredacted
    outcome: str  # "approved" or "rejected", set once a credit officer decides
    approved_draft_sha256: str
    related_guidance: RelatedGuidance
    model_calls: Annotated[list[ModelCall], operator.add]
    tool_calls: Annotated[list[ToolCall], operator.add]  # through the tool gateway
    completed: Annotated[list[str], operator.add]
    error: str
    error_node: str


def _traced(name: str, fn: Callable[[MemoState], Awaitable[dict[str, Any]]]) -> Any:
    return traced(name, fn, EXPECTED_ERRORS, APP_ID)


def draft_facts(state: MemoState, hits: list[EvidenceHit], policy: PolicyThresholds) -> DraftFacts:
    """What the output guardrails compare a draft with: the extracted figures and their
    lines, the computed ratios (also as percentages), the policy thresholds, and the numbers
    in the retrieved passages and the reconciliation details."""
    financials = state["financials"]
    figures = [f for fields in financials.years.values() for f in fields.values()]
    figures += financials.loan_figures()
    derived: list[Decimal] = [Decimal(y) for y in financials.years]
    for r in state["ratios"]:
        if r.value is not None:
            derived += [r.value, r.value * 100]  # a ratio may be shown as a percentage
    for threshold in (t.value for t in policy.thresholds.values()):
        derived += [threshold, threshold * 100]
    return DraftFacts(
        documents=state["package"].documents,
        sourced=tuple(SourcedValue(f.value, f.source.doc_id, f.source.line) for f in figures),
        derived=tuple(derived),
        texts=tuple(h.text for h in hits) + tuple(c.detail for c in state["checks"]),
        subject_name=state["package"].borrower_name,
    )


def build_workflow(ctx: AgentContext, checkpointer: BaseCheckpointSaver[Any] | None = None) -> Any:
    async def intake(state: MemoState) -> dict[str, Any]:
        package = state["package"]
        # Input guardrails see the documents before they are indexed or shown to a model.
        # Redaction runs first; the rest of the job sees only the redacted documents.
        checked, screened = await ctx.guardrails.screen(
            "input",
            Subject(
                state["job_id"],
                "intake",
                documents=package.documents,
                subject_name=package.borrower_name,
            ),
        )
        if any(r.action == "quarantined" for r in checked):
            raise DocumentQuarantined(checked)
        package = package.model_copy(update={"documents": screened.documents})
        redacted = pii_protected(checked)
        with sensitivity("standard" if redacted else "pii_unredacted"):
            hashes = await ctx.evidence.ingest(state["job_id"], package.documents)
            fs, _ = await run_intake(ctx, package)
        return {
            "package": package,
            "pii_redacted": redacted,
            "source_hashes": hashes,
            "financials": fs,
            "checks": reconcile(fs),
            "guardrails": checked,
        }

    async def analyst(state: MemoState) -> dict[str, Any]:
        ratios, flags, narrative, _ = await run_analyst(
            ctx, state["package"], state["financials"], state["checks"]
        )
        return {"ratios": ratios, "risk_flags": flags, "narrative": narrative}

    async def drafter(state: MemoState) -> dict[str, Any]:
        reviews = state.get("reviews", [])
        revision = (state["draft"], reviews) if reviews else None
        inputs = (
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
        draft, hits, _ = await run_drafter(*inputs)
        checked = await check_draft(state, draft, hits)
        fixes = state.get("automated_fixes", 0)
        if any(fails(r) for r in checked):
            # One automated fix, counted apart from the credit officer's revisions.
            fixes += 1
            draft, hits, _ = await run_drafter(
                *inputs, fix=(draft, problems(checked, draft, DECISION_MESSAGES))
            )
            second = await check_draft(state, draft, hits)
            checked += second
            if any(fails(r) for r in second):
                raise GuardrailFailed(checked, second, fixes)
        return {"draft": draft, "evidence": hits, "guardrails": checked, "automated_fixes": fixes}

    async def check_draft(
        state: MemoState, draft: MemoDraft, hits: list[EvidenceHit]
    ) -> list[GuardrailResult]:
        subject = Subject(
            state["job_id"],
            "drafter",
            draft=draft,
            facts=draft_facts(state, hits, ctx.policy),
            subject_name=state["package"].borrower_name,
        )
        return [
            *await ctx.guardrails.run("generation", subject),
            *await ctx.guardrails.run("output", subject),
        ]

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

    async def finalize(state: MemoState) -> dict[str, Any]:
        last = state["reviews"][-1]
        if last.decision != "approve":  # unreachable by routing; a hard stop all the same
            raise ReviewError("finalize needs an approval")
        draft = state["draft"]
        with acting_as(f"{APP_ID}.finalize"), collect_tool_calls() as tools:
            guidance = await related_guidance(ctx, state)
        return {
            "outcome": "approved",
            "approved_draft_sha256": hashlib.sha256(draft.model_dump_json().encode()).hexdigest(),
            "related_guidance": guidance,
            "tool_calls": list(tools),
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

    graph = StateGraph(MemoState)
    nodes = (
        ("intake", intake),
        ("analyst", analyst),
        ("drafter", drafter),
        ("compliance", compliance),
    )
    for name, fn in nodes:
        graph.add_node(name, _traced(name, fn))
    graph.add_edge(START, "intake")
    for name in NODE_ORDER:
        graph.add_conditional_edges(name, supervisor(name))
    # Not wrapped in _traced: interrupt() re-runs this node on resume, and it makes no calls.
    graph.add_node("human_review", cast(Any, review_node(MAX_HUMAN_REVISIONS)))
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
                "tool_calls": [],
                "completed": [],
                "reviews": [],
                "human_revisions": 0,
                "automated_fixes": 0,
                "guardrails": [],
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
