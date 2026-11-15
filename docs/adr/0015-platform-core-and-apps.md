# ADR 0015: A platform core with apps on top

**Status:** Accepted as the direction (Nov 15, 2026). The restructure itself is deferred until a second app is chosen or phase 4 starts, whichever comes first.

## Context

Axiom was built around one use case: a commercial-lending credit memo. By week 7, most of what makes it trustworthy has nothing to do with lending:

- role keys and separation of duties;
- the MCP tool gateway, with a token per workflow step and a log of every call ([ADR 0014](0014-tool-gateway.md));
- layered guardrails: personal data masking, the injection screen, number and citation checks, fail-closed handling ([ADR 0004](0004-guardrail-placement.md));
- the job queue, checkpoints and the pause for a human decision;
- the hash-chained evidence log and the auditor's view;
- model routing by logical alias, recorded responses for a keyless demo, and traces;
- the evaluation harness and its release gates ([ADR 0008](0008-evaluation-thresholds.md)).

The long-term goal is for Axiom to host several agentic apps of this kind: workflows that read documents, call tools, draft a result for a person to decide on, and leave audit evidence. The credit memo becomes the first of them. The question is when to reshape the code for that.

Today, some generic packages already stand alone and some still import lending code:

| Package | Imports lending code today |
|---|---|
| `evidence/`, `gateway/`, `router/`, `telemetry/` | No |
| `guardrails/`, `mcp_clients/` | Domain types (`axiom.domain`) |
| `memory/` | Agent context (`axiom.agents.base`) |
| `api/`, `jobs/` | Domain types, the package catalog, agent context; `jobs/` also runs the lending workflow |

## Options considered

| Option | Strengths | Weaknesses |
|---|---|---|
| Stay a single app | Nothing to change | Every new use case is a copy; governance controls drift between copies |
| **Record the direction now; split when a second app is real** | Costs nothing before Gate 3; the core-app boundary is drawn from two real cases, not guessed from one | Some refactoring later; until then the boundary is a rule people must follow |
| Restructure into core and apps now | A clean structure from the start | With one app, the boundary is a guess; weeks of work before Gate 3 with no visible change for users |

## Decision

Treat Axiom as a **core** with **apps** on top, and restructure only when a second app exists.

**The core** (shared by every app):

| Area | Today |
|---|---|
| Identity: role keys, separation of duties | `api/` (auth) |
| Jobs, checkpoints, human review | `jobs/`, `memory/`, the review step in `graph/` |
| Evidence log and auditor view | `evidence/`, the evidence endpoint, the auditor pages |
| MCP tool gateway | `gateway/`, `mcp_clients/` |
| Guardrail framework and generic rails | `guardrails/` |
| Model routing, recordings, telemetry | `router/`, `telemetry/` |
| Evaluation harness and CI gates | `evals/` (runner and scoring), `scripts/check_*.py` |
| Keyless demo setup | `demo_keys.py`, `docker/` |

**An app** brings:

- its workflow (a LangGraph graph over its own typed state) and its agents and prompts;
- its deterministic tools, and its section of the gateway policy;
- its domain types and its result schema (for lending: the memo);
- its choice of guardrails and thresholds;
- its evaluation set, golden answers and gate thresholds;
- its views on the review page;
- its recordings for the keyless demo.

For the credit memo app that is `agents/`, `graph/workflow.py`, `domain.py`, `tools/`, `catalog.py`, `prompts/`, the credit policy and lending references, `data/synthetic/`, and the golden set.

**Until the restructure:**

1. New code in a core package does not import `agents`, `graph`, `domain`, `tools` or `catalog` where a small interface or a generic type does the job.
2. New configuration is written so an app section could be added later (for example, gateway policy grouped by caller), but no app layer is built ahead of need.
3. The public API keeps its current shape (`/v1/memos`). How several apps share the API, by route or by version, is decided at the restructure.

## Consequences

- Nothing changes for users or for Gate 3.
- Week 8–10 work (CI gates, deployment) is reviewed against rule 1, so the split doesn't get harder.
- The restructure will move lending code under an app package and replace direct imports in `api/`, `jobs/`, `guardrails/`, `mcp_clients/` and `memory/` with interfaces an app implements. The table above is its starting checklist.
- The principles every app on the platform follows, with the credit memo app checked against each, are in [platform design principles](../platform-principles.md).
- **Revisit when:** a second use case is chosen, or phase 4 begins.
