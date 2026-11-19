"""The model documentation review workflow as a LangGraph state graph (ADR 0016, app 2).

intake -> assessor -> provisions -> drafter -> human_review, then finalize, back to the
drafter, or the end. Code runs the checklist and decides the documentation status; a model
judges each present section and drafts the findings; the core's guardrails screen the
documents at intake and check every draft; a validator who did not submit the job decides.
"""

from __future__ import annotations

import hashlib
import operator
from collections.abc import Callable
from decimal import Decimal
from typing import Annotated, Any, TypedDict

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command
from opentelemetry.trace import Status, StatusCode

from axiom.apps.model_review import domain
from axiom.apps.model_review.agents import ReviewContext, run_assessor, run_drafter
from axiom.apps.model_review.domain import (
    Completeness,
    DocumentationPackage,
    DocumentationStatus,
    FindingsReport,
    ReviewDecision,
    SectionAssessment,
    SectionCheck,
)
from axiom.apps.model_review.guardrails import DECISION_MESSAGES
from axiom.apps.model_review.tools.checklist import (
    check_package,
    completeness,
    documentation_status,
)
from axiom.core.types import MAX_HUMAN_REVISIONS, ModelCall, ReviewError
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
from axiom.guardrails.base import DraftFacts
from axiom.guardrails.output import problems
from axiom.mcp_clients.client import ToolCall
from axiom.mcp_clients.governance import RelatedProvision
from axiom.memory.checkpoint import serializer
from axiom.telemetry.setup import tracer

NODE_ORDER = ("intake", "assessor", "provisions", "drafter")


class ReviewState(TypedDict, total=False):
    job_id: str
    package: DocumentationPackage
    source_hashes: dict[str, str]
    checks: list[SectionCheck]
    completeness: Completeness
    assessments: list[SectionAssessment]
    documentation_status: DocumentationStatus
    # Provisions from the governance library for each item with a gap or a weak section.
    provisions: dict[str, list[RelatedProvision]]
    provisions_status: str  # "attached" or "not_evaluated: <reason>"
    report: FindingsReport
    reviews: Annotated[list[ReviewDecision], operator.add]
    human_revisions: int
    automated_fixes: int
    guardrails: Annotated[list[GuardrailResult], operator.add]
    pii_redacted: bool
    outcome: str
    approved_report_sha256: str
    model_calls: Annotated[list[ModelCall], operator.add]
    tool_calls: Annotated[list[ToolCall], operator.add]
    completed: Annotated[list[str], operator.add]
    error: str
    error_node: str


def draft_facts(state: ReviewState) -> DraftFacts:
    """What the output checks compare a report with: the documents, and the counts code
    computed (present, total, the score also as a percentage)."""
    score = state["completeness"]
    derived = [Decimal(score.present), Decimal(score.total), score.score, score.score * 100]
    texts = tuple(p.citation for found in state.get("provisions", {}).values() for p in found)
    return DraftFacts(
        documents=state["package"].documents,
        derived=tuple(derived),
        texts=texts,
        subject_name=state["package"].model_name,
    )


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def build_workflow(ctx: ReviewContext, checkpointer: BaseCheckpointSaver[Any] | None = None) -> Any:
    async def intake(state: ReviewState) -> dict[str, Any]:
        package = state["package"]
        checked, screened = await ctx.guardrails.screen(
            "input",
            Subject(
                state["job_id"],
                "intake",
                documents=package.documents,
                subject_name=package.model_name,
            ),
        )
        if any(r.action == "quarantined" for r in checked):
            raise DocumentQuarantined(checked)
        package = package.model_copy(update={"documents": screened.documents})
        checks = check_package(package, ctx.checklist)
        return {
            "package": package,
            "pii_redacted": pii_protected(checked),
            "source_hashes": {d.doc_id: _sha(d.text) for d in package.documents},
            "checks": checks,
            "completeness": completeness(checks, ctx.checklist),
            "guardrails": checked,
        }

    async def assessor(state: ReviewState) -> dict[str, Any]:
        assessments = await run_assessor(ctx, state["package"], state["checks"])
        return {
            "assessments": assessments,
            "documentation_status": documentation_status(state["completeness"], assessments),
        }

    async def provisions(state: ReviewState) -> dict[str, Any]:
        """The checklist provisions behind each gap or weak section, from the governance
        library. A failed or missing lookup is recorded and never stops the job: the report
        still cites the provisions by ID from the checklist."""
        flagged = [c.item_id for c in state["checks"] if c.outcome != "present"]
        flagged += [a.item_id for a in state["assessments"] if a.adequacy == "weak"]
        if not flagged:
            return {"provisions": {}, "provisions_status": "attached"}
        if ctx.governance is None:
            return {"provisions": {}, "provisions_status": "not_evaluated: no governance library"}
        items = {i.item_id: i for i in ctx.checklist.items}
        found: dict[str, list[RelatedProvision]] = {}
        try:
            for item_id in flagged:
                item = items[item_id]
                wanted = {(p.framework, p.provision_id) for p in item.provisions}
                query = f"{item.title}. {item.expects}"
                hits = await ctx.governance.lookup(query, k=8)
                found[item_id] = [h for h in hits if (h.framework, h.provision_id) in wanted]
        except Exception as exc:  # the lookup must never stop a review
            return {"provisions": {}, "provisions_status": f"not_evaluated: {type(exc).__name__}"}
        return {"provisions": found, "provisions_status": "attached"}

    async def drafter(state: ReviewState) -> dict[str, Any]:
        reviews = state.get("reviews", [])
        args = (
            ctx,
            state["package"],
            state["checks"],
            state["completeness"],
            state["assessments"],
            state["documentation_status"],
            state.get("provisions", {}),
        )
        revision = (state["report"], reviews) if reviews else None
        report = await run_drafter(*args, revision=revision)
        checked = await check_report(state, report)
        fixes = state.get("automated_fixes", 0)
        if any(fails(r) for r in checked):
            fixes += 1
            fix = (report, problems(checked, report, DECISION_MESSAGES))
            report = await run_drafter(*args, revision=revision, fix=fix)
            second = await check_report(state, report)
            checked += second
            if any(fails(r) for r in second):
                raise GuardrailFailed(checked, second, fixes)
        return {"report": report, "guardrails": checked, "automated_fixes": fixes}

    async def check_report(state: ReviewState, report: FindingsReport) -> list[GuardrailResult]:
        subject = Subject(
            state["job_id"],
            "drafter",
            draft=report,
            facts=draft_facts(state),
            subject_name=state["package"].model_name,
        )
        return [
            *await ctx.guardrails.run("generation", subject),
            *await ctx.guardrails.run("output", subject),
        ]

    async def finalize(state: ReviewState) -> dict[str, Any]:
        if state["reviews"][-1].decision != "approve":  # unreachable by routing
            raise ReviewError("finalize needs an approval")
        report = state["report"].model_dump_json()
        return {
            "outcome": "approved",
            "approved_report_sha256": _sha(report),
            "completed": ["finalize"],
        }

    def supervisor(after: str) -> Callable[[ReviewState], str]:
        def route(state: ReviewState) -> str:
            if state.get("error"):
                return END
            i = NODE_ORDER.index(after)
            return NODE_ORDER[i + 1] if i + 1 < len(NODE_ORDER) else "human_review"

        return route

    graph = StateGraph(ReviewState)
    for name, fn in (
        ("intake", intake),
        ("assessor", assessor),
        ("provisions", provisions),
        ("drafter", drafter),
    ):
        graph.add_node(name, traced(name, fn, CORE_EXPECTED_ERRORS))  # type: ignore[call-overload]
    graph.add_edge(START, "intake")
    for name in NODE_ORDER:
        graph.add_conditional_edges(name, supervisor(name))
    graph.add_node("human_review", review_node(MAX_HUMAN_REVISIONS))  # type: ignore[call-overload]
    graph.add_node("finalize", finalize)
    graph.add_conditional_edges("human_review", after_review)
    graph.add_edge("finalize", END)
    saver = checkpointer or InMemorySaver(serde=serializer((domain,)))
    return graph.compile(checkpointer=saver)


async def run_workflow(
    ctx: ReviewContext,
    job_id: str,
    package: DocumentationPackage,
    checkpointer: BaseCheckpointSaver[Any] | None = None,
) -> ReviewState:
    workflow = build_workflow(ctx, checkpointer)
    with tracer().start_as_current_span("workflow model_review") as span:
        span.set_attribute("axiom.job_id", job_id)
        span.set_attribute("axiom.package_id", package.package_id)
        state: ReviewState = await workflow.ainvoke(
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
    ctx: ReviewContext,
    job_id: str,
    review: ReviewDecision,
    checkpointer: BaseCheckpointSaver[Any],
) -> ReviewState:
    workflow = build_workflow(ctx, checkpointer)
    with tracer().start_as_current_span("workflow resume") as span:
        span.set_attribute("axiom.job_id", job_id)
        snapshot = await workflow.aget_state(thread(job_id))
        if snapshot.next != ("human_review",):
            raise ReviewError(f"job {job_id} is not paused for review")
        state: ReviewState = await workflow.ainvoke(
            Command(resume=review.model_dump(mode="json")), thread(job_id)
        )
        if state.get("error"):
            span.set_status(Status(StatusCode.ERROR, state.get("error_node", "")))
        return state


def paused_for_review(state: ReviewState) -> bool:
    return "__interrupt__" in state
