"""Workflow pieces every app's graph shares (ADR 0016): per-node tracing and evidence
collection, the guardrail outcomes that stop a job, and the pause for a reviewer's decision.

An app's graph is its own; these keep the controls around each node the same in every app.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from langgraph.types import interrupt
from opentelemetry.trace import Status, StatusCode

from axiom.core.agents import AgentOutputError, collect_calls, sensitivity
from axiom.core.types import ReviewDecision, ReviewError
from axiom.guardrails import GuardrailResult
from axiom.mcp_clients.client import acting_as, collect_tool_calls
from axiom.router.types import FailClosed, ProviderError, Sensitivity
from axiom.telemetry.setup import tracer


class DocumentQuarantined(RuntimeError):
    """An input guardrail found instructions aimed at the AI. No model sees the input."""

    def __init__(self, guardrails: list[GuardrailResult]) -> None:
        blocked = [r.control for r in guardrails if r.action == "quarantined"]
        super().__init__(f"quarantined by {', '.join(blocked)}")
        self.state_update: dict[str, Any] = {"guardrails": guardrails}


class GuardrailFailed(RuntimeError):
    """A draft still failed a guardrail after its one automated fix."""

    def __init__(
        self, guardrails: list[GuardrailResult], last: list[GuardrailResult], automated_fixes: int
    ) -> None:
        failed = sorted({r.control for r in last if fails(r)})
        super().__init__(f"draft failed {', '.join(failed)} after an automated fix")
        self.state_update: dict[str, Any] = {
            "guardrails": guardrails,
            "automated_fixes": automated_fixes,
        }


# The failures a node reports as the job's outcome rather than raising. An app adds its own.
CORE_EXPECTED_ERRORS: tuple[type[Exception], ...] = (
    AgentOutputError,
    ProviderError,
    FailClosed,
    DocumentQuarantined,
    GuardrailFailed,
)


def fails(result: GuardrailResult) -> bool:
    """A generation or output check that found a problem. not_evaluated sends nothing back."""
    return result.stage in ("generation", "output") and result.outcome == "triggered"


Node = Callable[[Any], Awaitable[dict[str, Any]]]


def traced(name: str, fn: Node, expected: tuple[type[Exception], ...], app_id: str) -> Node:
    """Run a node in its span, with its model calls and tool calls collected for the
    evidence log, its calls marked by whether the documents were redacted, and its tool
    calls made as this step (`<app_id>.<step>` at the tool gateway). An expected failure ends
    the job with its reason."""

    async def run(state: Mapping[str, Any]) -> dict[str, Any]:
        with tracer().start_as_current_span(f"node {name}") as span:
            span.set_attribute("axiom.job_id", state["job_id"])
            span.set_attribute("axiom.node", name)
            level: Sensitivity = "standard" if state.get("pii_redacted") else "pii_unredacted"
            with (
                collect_calls() as calls,
                collect_tool_calls() as tools,
                sensitivity(level),
                acting_as(f"{app_id}.{name}"),
            ):
                try:
                    update = await fn(state)
                except expected as exc:
                    span.set_status(Status(StatusCode.ERROR, type(exc).__name__))
                    # Keep the attempts that failed, and the guardrail results that stopped
                    # the node: the evidence log records both.
                    return {
                        "error": f"{type(exc).__name__}: {exc}",
                        "error_node": name,
                        "model_calls": list(calls),
                        "tool_calls": list(tools),
                        **getattr(exc, "state_update", {}),
                    }
            span.set_attribute("axiom.node.model_calls", len(calls))
            return {
                **update,
                "model_calls": list(calls),
                "tool_calls": list(tools),
                "completed": [name],
            }

    return run


def review_node(max_revisions: int) -> Node:
    """The pause for a reviewer. On resume, interrupt() returns the decision: revise counts
    a human revision (at most `max_revisions`), reject sets the outcome. Not traced: it
    re-runs on resume and makes no calls."""

    async def human_review(state: Mapping[str, Any]) -> dict[str, Any]:
        decision = ReviewDecision.model_validate(
            interrupt({"round": len(state.get("reviews", [])) + 1})
        )
        update: dict[str, Any] = {"reviews": [decision]}
        if decision.decision == "revise":
            done = state.get("human_revisions", 0)
            if done >= max_revisions:
                raise ReviewError(f"at most {max_revisions} human revisions")
            update["human_revisions"] = done + 1
        elif decision.decision == "reject":
            update["outcome"] = "rejected"
        return update

    return human_review


def after_review(state: Mapping[str, Any]) -> str:
    """Where a decision leads: approve to finalize, revise back to the drafter, else the end."""
    from langgraph.graph import END

    decision = state["reviews"][-1].decision
    return {"approve": "finalize", "revise": "drafter"}.get(decision, END)


def thread(job_id: str) -> dict[str, Any]:
    return {"configurable": {"thread_id": job_id}}
