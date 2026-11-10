# ADR 0001: Orchestration framework

**Status:** Accepted (week 2)

## Context

The memo workflow has a fixed sequence: intake, analysis, drafting, compliance review, human review, finalize. It needs:

1. A **repeatable, auditable path**: the same input follows the same transitions, and every transition can be traced.
2. A **human pause that can last days**: the job stops at review, survives a restart, and resumes when a reviewer responds through the API.
3. **Typed shared state** (the borrower package, ratios, draft, findings) that each step reads and writes.
4. **Bounded loops**: revision goes back to drafting at most twice; automated fixes at most once per draft.
5. **Per-step observability**: one OpenTelemetry span per step, model call and tool call.

## Options considered

| Option | Strengths | Weaknesses |
|---|---|---|
| LangGraph | Explicit state graph with typed state; built-in interrupts for human input; Postgres checkpointer for pause and resume; conditional edges written as plain code; already used in sibling repos | Ties graph wiring to the LangChain ecosystem; API changes between versions |
| AutoGen | Strong for multi-agent conversation and agent-to-agent negotiation | Control flow emerges from the conversation, which makes a fixed, auditable path harder to guarantee; durable human pause is not its central model |
| Semantic Kernel | Good enterprise integration; plugin model; multi-language | Agent orchestration less mature for durable, checkpointed workflows; more natural in .NET than in this Python stack |
| CrewAI | Fast to prototype role-based agent crews | Designed around autonomous delegation between agents, the opposite of the rule-based control this workflow requires; less control over state and transitions |

Microsoft has since announced Agent Framework as the successor to AutoGen and Semantic Kernel. That does not change the comparison, because the deciding needs are durable interrupts and a fixed, code-defined path.

## Decision

Use **LangGraph** for orchestration.

- The graph is a fixed pipeline. A rule-based supervisor (plain code, no LLM) picks the next node. ADR 0011 records that choice in detail.
- Human review is a LangGraph interrupt, resumed by `POST /v1/memos/{job_id}/review`.
- State is checkpointed in Postgres, keyed by `job_id`.
- Graph wiring lives in `src/axiom/graph/`. Each node's logic lives in `src/axiom/agents/` as ordinary functions that take and return typed models, so the logic does not depend on LangGraph.

## Consequences

- Pausing at review for days and surviving a container restart come from the checkpointer, not custom code. This is tested in week 4.
- Every transition is code, so it can be unit-tested and appears as a span in the trace.
- Keeping node logic outside the graph module limits lock-in: moving to another orchestrator means rewriting the wiring, not the agents.
- LangGraph is pinned in `uv.lock`. Upgrades go through CI like any other dependency change.
- The framework's support for agents choosing their own next step is deliberately unused in the MVP.
