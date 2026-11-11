"""The memo workflow as a LangGraph state graph (ADR 0001).

intake -> analyst -> drafter -> compliance. A rule-based supervisor, plain code with no
model, picks each transition: on to the next node, or to the end when a node fails.
Human review and finalize join next.
"""

from __future__ import annotations

import operator
from collections.abc import Awaitable, Callable
from typing import Annotated, Any, TypedDict

from langgraph.graph import END, START, StateGraph
from opentelemetry.trace import Status, StatusCode

from axiom.agents.analyst import run_analyst
from axiom.agents.base import AgentContext, AgentOutputError, ModelCall, collect_calls
from axiom.agents.drafter import run_drafter
from axiom.agents.intake import IntakeError, run_intake
from axiom.domain import (
    BorrowerPackage,
    CheckResult,
    ComplianceFinding,
    FinancialStatements,
    MemoDraft,
    RatioResult,
    RiskFlag,
)
from axiom.mcp_clients.evidence import EvidenceHit
from axiom.router.types import FailClosed, ProviderError
from axiom.telemetry.setup import tracer
from axiom.tools.policy_rules import RuleInput, evaluate
from axiom.tools.reconcile import reconcile

NODE_ORDER = ("intake", "analyst", "drafter", "compliance")
EXPECTED_ERRORS = (IntakeError, AgentOutputError, ProviderError, FailClosed)


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


def build_workflow(ctx: AgentContext) -> Any:
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
        draft, hits, _ = await run_drafter(
            ctx,
            state["job_id"],
            state["package"],
            state["financials"],
            state["ratios"],
            state["risk_flags"],
            state["checks"],
            state["narrative"],
        )
        return {"draft": draft, "evidence": hits}

    async def compliance(state: MemoState) -> dict[str, Any]:
        findings = evaluate(
            RuleInput(
                package=state["package"],
                financials=state["financials"],
                checks=state["checks"],
                flags=state["risk_flags"],
                draft=state["draft"],
                policy=ctx.policy,
            )
        )
        return {"findings": findings}

    def supervisor(after: str) -> Callable[[MemoState], str]:
        """Rule-based routing: stop on error, otherwise the next node in the fixed order."""

        def route(state: MemoState) -> str:
            if state.get("error"):
                return END
            i = NODE_ORDER.index(after)
            return NODE_ORDER[i + 1] if i + 1 < len(NODE_ORDER) else END

        return route

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
    return graph.compile()


async def run_workflow(ctx: AgentContext, job_id: str, package: BorrowerPackage) -> MemoState:
    workflow = build_workflow(ctx)
    with tracer().start_as_current_span("workflow memo") as span:
        span.set_attribute("axiom.job_id", job_id)
        span.set_attribute("axiom.package_id", package.package_id)
        state: MemoState = await workflow.ainvoke(
            {"job_id": job_id, "package": package, "model_calls": [], "completed": []}
        )
        if state.get("error"):
            span.set_status(Status(StatusCode.ERROR, state.get("error_node", "")))
        return state
