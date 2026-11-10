# Architecture

This document explains how the platform is put together: its layers, how a memo job flows through them, and where each piece lives in the code. The [development plan](dev-plan.md) holds the full specification; the [ADRs](adr/README.md) hold the reasoning behind each decision; the [threat model](threat-model.md) covers what can go wrong.

**Status (Nov 10, 2026):** the supporting services (Postgres, Phoenix, OpenTelemetry Collector, regulus-mcp, gkn-mcp) run under `docker compose` and pass the Gate 1 check. The API, worker, agents, router and guardrails are designed here and built from week 3. Section 7 tracks each component.

## 1. Overview

![Reference architecture](assets/architecture.png)

Requests flow top to bottom through five layers. Observability and evidence cut across all of them.

| Layer | What it does | Code |
|---|---|---|
| **API** | The stable `/v1` contract: jobs, memos, reviews, evidence. Authentication, roles, rate limits, idempotency | `src/axiom/api/` |
| **Jobs** | Job records and the worker loop that runs the workflow | `src/axiom/jobs/` |
| **Workflow** | A fixed LangGraph pipeline of specialist agents, with a human review pause | `src/axiom/graph/`, `src/axiom/agents/` |
| **Controls** | Guardrails at input, generation, output and process stages | `src/axiom/guardrails/` |
| **Capabilities** | Model router, deterministic tools, MCP services | `src/axiom/router/`, `src/axiom/tools/`, `src/axiom/mcp_clients/` |
| Cross-cutting | Telemetry, evidence log, checkpointed memory | `src/axiom/telemetry/`, `src/axiom/evidence/`, `src/axiom/memory/` |

Three rules shape everything:

1. **The API is the contract.** Clients see jobs and memos. They never see model, agent or prompt names; those appear only in the auditor-only evidence endpoint. Everything behind `/v1` can change without breaking clients.
2. **LLMs reason; code calculates.** Models extract, write and review. Every number comes from extraction with a source location or from a deterministic tool.
3. **Humans decide.** The workflow cannot finalize a memo without approval from an authorized reviewer who did not submit it.

## 2. A memo job, end to end

```mermaid
sequenceDiagram
    autonumber
    actor RM as Relationship manager
    actor CO as Credit officer
    participant API as /v1 API
    participant DB as Postgres
    participant W as Worker + graph
    participant GKN as gkn-mcp
    participant M as Model router
    participant REG as regulus-mcp

    RM->>API: POST /v1/memos (package, Idempotency-Key)
    API->>DB: store job (queued)
    API-->>RM: 202 Accepted, job_id
    W->>DB: claim job (running)
    W->>W: input guardrails (PII redaction, injection screen)
    W->>GKN: ingest_documents(job_id, docs)
    W->>M: intake: extract figures (structured output)
    W->>W: analyst: compute ratios in code
    W->>GKN: retrieve_evidence(job_id, ...)
    W->>M: drafter: memo with citations
    W->>W: output guardrails (groundedness, number provenance)
    W->>M: compliance: findings vs policy rules and lending refs
    W->>DB: checkpoint, state awaiting_review
    RM->>API: GET /v1/memos/{job_id}
    API-->>RM: draft memo, flags, findings
    CO->>API: POST /v1/memos/{job_id}/review (approve)
    API->>DB: record decision (reviewer ≠ submitter)
    W->>REG: lookup_provisions (related AI governance guidance)
    W->>DB: finalize memo, write evidence bundle
```

Every step writes OpenTelemetry spans (traces go to Phoenix) and evidence-log events.

## 3. The workflow

```mermaid
flowchart LR
    A[Intake] --> B[Financial analyst]
    B --> C[Memo drafter]
    C --> D[Compliance reviewer]
    D --> E{{Human review}}
    E -- approve --> F[Finalize + evidence]
    E -- revise, max 2 --> C
    E -- reject --> G[Closed]
```

| Node | Does | Uses a model for | Uses code for |
|---|---|---|---|
| Intake | Parses documents into a typed `BorrowerPackage` and indexes them for this job only | Extracting figures (structured output) | Parsing; reconciliation (balance sheet balances, totals foot) |
| Financial analyst | Ratios, trends, risk flags, narrative | Narrative only | DSCR, leverage, liquidity, interest coverage, trends |
| Memo drafter | Fixed-template memo with a citation for every claim | Writing | Retrieval of evidence through GKN |
| Compliance reviewer | Findings with severity | Judging the draft against retrieved references | Credit policy rule engine |
| Human review | Pause until a reviewer acts | None | LangGraph interrupt, resumed by the review endpoint |
| Finalize | Stores the memo and evidence bundle | None | Evidence writer; Regulus lookup (informational) |

A rule-based supervisor, written in code with no LLM, chooses each transition, so the path is repeatable and auditable ([ADR 0001](adr/0001-orchestration-framework.md); ADR 0011 in week 3). Graph state is checkpointed in Postgres by `job_id`, so a job can wait at review for days and survive restarts.

**Loops are bounded.** A human can send a draft back for revision at most twice; after that, only approve or reject. An output-guardrail failure sends the draft back once as an automated fix; a second failure fails the job.

## 4. Controls

Guardrails run where each risk appears ([ADR 0004](adr/0004-guardrail-placement.md)):

| Stage | Controls |
|---|---|
| Input | PII redaction (financial values unchanged); prompt-injection screen on documents |
| Generation | Rails: no final-decision language, no investment advice, stay on task |
| Output | Groundedness and citation support; number provenance; harm categories; PII on output |
| Process | Human approval before finalize, from a reviewer who did not submit; cannot be switched off |

Each check returns `passed`, `triggered` or `not_evaluated`, emits a span and an evidence event, and can be toggled in `config/guardrails.yaml` (except human approval) so its effect can be measured. A check that cannot run never reports a pass.

## 5. Capabilities

**Model router** ([ADR 0005](adr/0005-model-routing.md)). Agents ask for a task route (`extraction`, `narrative`, `compliance`), never a model. The router resolves logical aliases from environment settings, applies sensitivity rules first (unredacted PII only to a permitted local model, otherwise fail closed), then the task route, then fallback. Adapters: Azure OpenAI, Anthropic, Ollama, and `recorded` for the keyless demo. Every call returns a `RoutingDecision` for the evidence bundle.

**Deterministic tools** ([ADR 0003](adr/0003-tool-integration.md)). Ratio math and policy rules are plain Python in `src/axiom/tools/`, called in-process and unit-tested to 100% exactness.

**MCP services** ([ADR 0003](adr/0003-tool-integration.md)). Two sibling repositories run as separate containers over streamable HTTP, behind a shared bearer secret, with host-header checks:

| Service | Tools | Role |
|---|---|---|
| gkn-mcp (geometric_knowledge_network v0.2.0) | `ingest_documents`, `retrieve_evidence`, `delete_job` | Job-scoped evidence; read-only `credit_policy` and `lending_refs` collections. Each job has its own index, so isolation is structural |
| regulus-mcp (Regulus v0.2.0) | `lookup_provisions`, `recommend_mitigants` | Related AI governance provisions for the evidence bundle. Runs offline from committed snapshots |

The platform sets scope arguments such as `job_id` from graph state; a model never chooses them. Regulus covers AI governance, not lending regulation, so it never reviews memo content ([ADR 0012](adr/0012-regulatory-position.md)).

## 6. Data, evidence and observability

**Postgres** holds three things: job records and idempotency keys, LangGraph checkpoints, and the evidence log.

**The evidence log** is append-only and hash-chained (SHA-256). For each memo the bundle records: input hashes, retrieved source hashes, policy version, model aliases and prompt versions, routing decisions, guardrail events, trace ID, image tag, and the reviewer's decision with both identities. The auditor-only endpoint returns the bundle and verifies the chain.

**Telemetry** follows the OpenTelemetry GenAI conventions: spans for each request, graph run, node, model call, tool call and guardrail check, with tokens, cost, latency and outcome. Locally the Collector forwards to Phoenix (`http://localhost:6006`); in the cloud it forwards to Azure Monitor. Traces carry IDs and counts, not document text.

**Data** is synthetic throughout: generated borrower packages with known ground truth, a fictional credit policy, and public lending references with a provenance manifest.

## 7. Deployment and component status

| Environment | Runtime | Notes |
|---|---|---|
| Local | Docker Compose (`docker-compose.yml`) | Keyless by default. All ports bound to 127.0.0.1. MCP images built from release tags |
| Cloud demo (week 9) | Azure Container Apps, Postgres Flexible Server, ACR, Key Vault, Log Analytics; Terraform | Catalog-only public access, no uploads, daily job cap. No local model in the cloud, so unredacted-PII routes fail closed |
| Kubernetes (phase 4) | kind + Helm | Packaging demonstration only |

| Component | Status |
|---|---|
| Postgres, Phoenix, OTel Collector | Running in compose |
| regulus-mcp, gkn-mcp | Running in compose; Gate 1 check passes 7/7 |
| MCP client (`MCPToolClient`) | Built and tested |
| API, job store, worker, graph skeleton, ratio tools, router v1, telemetry | Week 3 |
| Policy rule engine, human review, checkpointer, evidence log | Week 4 |
| Guardrails | Week 5 |
| Eval baseline | Week 6 |
| Evidence endpoint, review page, keyless demo | Week 7 |
| CI release gates | Week 8 |
| Cloud deployment | Week 9 |

## 8. Decisions

| ADR | Decision |
|---|---|
| [0001](adr/0001-orchestration-framework.md) | LangGraph, fixed pipeline, logic outside the graph module |
| [0002](adr/0002-api-interaction-model.md) | Async jobs with polling; webhooks in phase 4 |
| [0003](adr/0003-tool-integration.md) | MCP for sibling services; in-process calls for deterministic tools |
| [0004](adr/0004-guardrail-placement.md) | Layered guardrails at four stages |
| [0005](adr/0005-model-routing.md) | Thin in-house router with logical aliases |
| [0012](adr/0012-regulatory-position.md) | NIST AI RMF primary; SR 26-2 by analogy; rule engine plus cited lending references |

The remaining ADRs (0006–0011) are written in the week each decision is made.
