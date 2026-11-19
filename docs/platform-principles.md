# Platform design principles

Axiom is meant to become a platform that hosts many AI apps on one governed core ([ADR 0015](adr/0015-platform-core-and-apps.md)). The credit memo is app #1. This page holds the design principles every app on the platform should follow, and checks the first app against them.

It is a living page. Each section gives the principles, then an **In Axiom today** table: what the credit memo app already does, what it does in part, and what is not built yet. When a gap closes, or a principle changes, update the table and the change log at the end.

**Contents**

1. [Model choice and total cost of ownership](#1-model-choice-and-total-cost-of-ownership)
2. [API contract: naming and versioning](#2-api-contract-naming-and-versioning)
3. [LLM-as-judge practice](#3-llm-as-judge-practice)
4. [Multi-agent design](#4-multi-agent-design)
5. [Build, buy and operate](#5-build-buy-and-operate)
6. [Data for AI applications](#6-data-for-ai-applications)

---

## 1. Model choice and total cost of ownership

**Principles**

- **Choose models by evaluation on your own task, not public leaderboards.** Build a golden set of real examples before comparing models. A few hundred is typical; that set is the most portable asset you have.
- **Measure cost per successful task, not cost per token.** A cheap model that fails 15% of the time, and so triggers retries or human rework, usually costs more than a stronger one.
- **Use the smallest model that clears the quality bar, and route by task.** Small models for classification, extraction and routing; frontier models for reasoning and drafting.
- **Apply hard constraints before comparing quality:** where data may go (a hosted provider or self-hosted), residency, latency targets, context length, and how reliably the model uses tools.
- **Keep models behind an abstraction.** A router plus portable prompts lets you switch providers without touching the apps.
- **Re-evaluate on a schedule.** Models get deprecated, and in a regulated firm every forced upgrade means revalidation.

**What goes into total cost**

| Visible | Hidden |
|---|---|
| Input and output tokens, embeddings, the vector store | Retries, guardrail and judge calls |
| Hosting or GPUs, if self-hosted | Evaluation and red-team runs, monitoring |
| | Human review time |
| | Revalidation when a model is deprecated or upgraded |
| | Engineering and operations staff |

**Cost levers:** prompt caching, batch APIs for work that isn't urgent, tighter retrieval to shrink the context, routing, and fine-tuning or distilling a small model once volume is high and the task is stable. Self-hosting open-weight models pays off only with high, steady volume, strict data constraints, and a team that can keep GPUs busy.

**Choose the cheapest model that passes the evaluation gate for the task, and count the cost per completed task: retries, human review and revalidation included.**

**In Axiom today**

| Principle | Status | Where |
|---|---|---|
| Choose by evaluation on our own task | 🟡 Partly. A golden set of 25 synthetic packages with ground truth, and a live baseline of 75 runs. Smaller than a few hundred real examples, and the routes are provisional until the phase 4 capability benchmark | [golden set](../evals/golden/v1/answers.md), [ADR 0008](adr/0008-evaluation-thresholds.md), [`routing.local.yaml`](../config/routing.local.yaml) |
| Cost per successful task | ⬜ Not yet. Token counts are recorded for every model call, but cost is not computed, and the cost gate in ADR 0008 is "not yet gated" | evidence bundle; ADR 0008 |
| Smallest model that clears the bar, routed by task | 🟢 Built. A small model extracts; a frontier model writes the narrative, the draft and the compliance review; each route has a fallback | [ADR 0005](adr/0005-model-routing.md) |
| Hard constraints first | 🟢 Built for data: sensitivity rules run before the task route, and unredacted personal data may reach only a local model (an alias mapped to Ollama), or the job stops (fail closed) | ADR 0005 |
| Models behind an abstraction | 🟢 Built. Apps name logical aliases (`small-a`, `frontier-a`); real deployments live only in the environment. Three provider adapters (OpenAI and Azure OpenAI, Anthropic, Ollama), mixed alias by alias; only OpenAI-family models have been run live so far | `src/axiom/router/`, [use your own models](../README.md#use-your-own-models) |
| Re-evaluate on a schedule | 🟡 Partly. The live smoke gate runs when prompts, agents, the workflow or routing change; the full golden set runs on demand, not on a schedule | [live gates](../.github/workflows/live-gates.yml) |

---

## 2. API contract: naming and versioning

**Naming**

- Design the contract first (OpenAPI). Resources are plural, lowercase nouns, with kebab-case paths.
- Pick one casing for JSON fields and use it everywhere.
- Actions are HTTP methods, not words in the URL. Where a noun doesn't fit, use a sub-resource.

| Do | Don't |
|---|---|
| `POST /v1/credit-memos` | `/createMemo` |
| `GET /v1/credit-memos/{memo_id}` | `/getMemoById?id=` |
| `POST /v1/credit-memos/{id}/approvals` | `/approveMemo` |

**Versioning**

- Put only the major version in the URL (`/v1/`). Additive changes don't get a new version.
- Write down what breaks a client:
  - **Safe:** a new optional field, a new endpoint, a new optional parameter.
  - **Breaking:** removing or renaming a field, changing a type, making an optional field required, changing a default or a meaning, changing error codes.
  - **Adding an enum value** is safe only if clients are told to tolerate unknown values. The standard says so.
- **The model and prompt version is not the API version.** A model swap is controlled by evaluation gates. Only a change to the output schema, or to what it means, needs a new API version. Record the model, prompt version and trace id for audit; clients must not depend on them.
- **Deprecate with notice:** announce it, send `Deprecation` and `Sunset` headers, run both versions side by side for a set period (6 to 12 months is common), and track use per version to know when one can be retired.

**Other standards**

- Long-running AI work is asynchronous: `POST` returns `202 Accepted` with a job id and a `Location` header, and the client polls or receives a signed webhook.
- An idempotency key on every `POST` that does consequential work.
- One error format everywhere: RFC 9457 problem details, with stable error codes.
- `429` with `Retry-After` for rate limits, cursor pagination, a correlation id on every request, and OAuth2 or managed identity.

**Callers depend on the contract, never on which model or agent sits behind it. Changing a model is a release-gate decision, not an API version.**

**In Axiom today**

| Principle | Status | Where |
|---|---|---|
| Contract first, checked | 🟢 Built. The OpenAPI document is the published contract, and Schemathesis checks every response against it in CI | [contract tests](../tests/test_contract.py) |
| Plural nouns, one casing, methods not verbs | 🟡 Mostly. `/v1/memos` and snake_case throughout. Two paths break the rule: `POST /v1/memos/{id}/review` (singular; `reviews` would match) and `GET /v1/policy/{ref}` (singular). Fix them in `/v2`, or add the plural paths alongside | `src/axiom/api/app.py` |
| Major version in the URL only | 🟢 Built. `/v1` only | |
| What breaks a client, written down | ⬜ Not yet. Needs a short versioning standard, including how clients treat unknown enum values (job statuses, recommendations) | |
| Model and prompt are not the API version | 🟢 Built, more strictly than the principle asks. Public responses never name a model, agent or prompt; the model alias, provider, prompt version and trace id are in the auditor-only evidence endpoint | [architecture](architecture.md), `GET /v1/memos/{id}/evidence` |
| Deprecation policy and headers | ⬜ Not yet. Only one version exists | |
| Asynchronous jobs: 202, job id, `Location` | 🟢 Built. Polling only; no webhooks yet | ADR 0002 |
| Idempotency keys on consequential `POST`s | 🟢 Built. Both `POST`s require one, and the OpenAPI document says so | |
| One error format with stable codes | 🟢 Built. RFC 9457 problem details; each code documented | [api-errors.md](api-errors.md) |
| `429` with `Retry-After` | ⬜ Not yet. Per-key rate limits and token caps are open in the threat model | [threat model](threat-model.md) |
| Cursor pagination | ⬜ Not yet. `GET /v1/memos` takes a `limit` only | |
| Correlation id on every request | 🟡 Partly. Each job has a trace id, shown to auditors; requests don't carry a correlation id | |
| OAuth2 or managed identity | 🟡 Partly. Role-scoped API keys today; managed identity is week 9 (cloud deployment) | [dev plan](dev-plan.md) |

---

## 3. LLM-as-judge practice

**Principles**

- **Use deterministic checks first.** Schema validity, exact numbers, whether citations exist, policy rules: code checks these better. Use an LLM judge only for what code can't check: faithfulness, relevance, completeness, tone.
- **Calibrate against people before trusting the judge.** Label a sample by hand (100 to 200 is common), then measure agreement: Cohen's kappa, or precision and recall on failures. Set the acceptance bar before the judge becomes a gate.
- **Write narrow rubrics.** One criterion per judge call. Binary or 3-point scales, not 1 to 10. The judge cites its evidence before its verdict, and gets a reference answer when there is one.
- **Control known biases:**

  | Bias | Fix |
  |---|---|
  | Position bias, in pairwise comparisons | Swap the order and average |
  | Preferring longer answers | Tell the rubric to ignore length; normalize |
  | Preferring its own output | A judge from a different model family than the generator |
  | Leniency | Clear failure examples in the prompt |
  | Shifting the verdict with the stakes of the label | Hold the content fixed and vary the stated stakes; the verdict must not move |

- **Govern the judge like any other model.** Pin its model and prompt version and record them in the inventory. Changing the judge resets the baselines.
- **Make results stable:** temperature 0, and for high-stakes gates several samples with a majority vote.
- **Keep auditing it.** People re-check a small share of production verdicts (around 5%), and the judge is recalibrated when agreement slips.
- **Account for noise.** Small golden sets give noisy scores. Report confidence intervals and set thresholds above the noise.

**The judge is a model too: calibrated against human labels, version-pinned, bias-controlled and audited. Otherwise you are measuring the judge, not the system.**

**In Axiom today**

| Principle | Status | Where |
|---|---|---|
| Deterministic checks first | 🟢 Built. Ratios, flags, number provenance, citation coverage and the alignment checks are code; every gate in CI is deterministic | [evals](../evals/README.md), [alignment](../evals/alignment/README.md) |
| Calibrated against people before gating | 🟡 Partly. The grader catches 10 of 11 deliberately altered items, but agreement with a person's labels is not measured yet, so grader scores are reported, not gated | [ADR 0008](adr/0008-evaluation-thresholds.md), [calibration](../evals/judge/v1/calibration-live.md) |
| Narrow rubrics | 🟢 Built. One judge call per finding and one per citation | [`grader.py`](../evals/judge/grader.py) |
| Not judging its own output | 🟡 Partly. The grader is a different model from the drafter, with no fallback to the drafter's model. A different model family is not configured yet | [`routing.local.yaml`](../config/routing.local.yaml) |
| Stakes don't move the verdict | 🟡 Partly. A high-stakes note changed none of 30 verdicts in calibration | calibration |
| Version-pinned and recorded | 🟢 Built. Judge prompts are versioned like every prompt, and each call is recorded | `evals/judge/prompts/` |
| Several samples, majority vote | ⬜ Not yet | |
| Production audit sample | ⬜ Not yet. There is no production yet | |
| Noise accounted for | 🟡 Partly. Tolerances come from the spread across three live repeats; no confidence intervals yet | ADR 0008 |

---

## 4. Multi-agent design

**Principles**

- **Start simple.** In order of preference: one model call, then a fixed workflow, then one agent with tools, then several agents. Add agents only for clear separation of duties, different permissions, or parallel work.
- **In regulated settings, orchestrate explicitly** (a graph or a supervisor) rather than letting agents converse freely. It is predictable, testable and auditable.
- **Typed contracts between agents.** Each agent has one job, with structured inputs and outputs. Agents pass structured artifacts to each other, not whole transcripts.
- **Deterministic tools for anything that must be exact:** arithmetic, lookups, policy rules. The model handles language and judgment.
- **Least privilege for each agent.** Tool allow-lists, scoped credentials, read and write kept apart. Actions with real consequences need approval.
- **Layer the memory:** working memory within a run, session memory, long-term memory. Retrieve from the knowledge base rather than piling everything into the context.
- **Limit execution:** maximum steps, maximum revision loops, timeouts, token budgets, loop detection.
- **A human at the points that matter**, with interrupts and checkpoints so a run can resume.
- **Handle failure deliberately:** retries with backoff, fallback models, tool calls that are safe to repeat, graceful degradation.
- **Trace and evaluate at every level:** each agent step and tool call; each component, the path an agent took, and the end-to-end result.
- **Treat anything retrieved or returned by a tool as untrusted input.** Keep instructions apart from data, to defend against injection hidden in documents.

**Agents only where they earn their place, deterministic tools for anything that must be exact, least privilege for each agent, hard limits on loops and spend, and a human at every consequential decision.**

**In Axiom today**

| Principle | Status | Where |
|---|---|---|
| Start simple; explicit orchestration | 🟢 Built. A fixed LangGraph workflow (intake, analyst, drafter, compliance, review), not free conversation | [ADR 0001](adr/0001-orchestration-framework.md), [ADR 0011](adr/0011-workflow-control.md) |
| Typed contracts between steps | 🟢 Built. Pydantic inputs and outputs, validated, with one retry, then the job fails | `src/axiom/apps/credit_memo/agents/` |
| Deterministic tools for exact work | 🟢 Built. Ratios, reconciliation and policy rules are tested code; models never calculate | [ADR 0007](adr/0007-deterministic-computation.md) |
| Least privilege | 🟢 Built. Each step may call only its own tools, enforced by the MCP tool gateway; role-scoped API keys; the submitter can't review their own job | [ADR 0014](adr/0014-tool-gateway.md) |
| Layered memory, retrieval over stuffing | 🟢 Built for this app: job-scoped retrieval from GKN, state checkpointed in Postgres | [ADR 0006](adr/0006-memory-architecture.md) |
| Execution limits | 🟡 Partly. At most two human revisions and one automated fix; tool calls time out. No token budget per job or overall step cap yet | [architecture](architecture.md) |
| Human at the consequential decision | 🟢 Built. Every memo stops for a credit officer at a LangGraph interrupt, and resumes after a restart | ADR 0011 |
| Failure handling | 🟡 Partly. A fallback model per route, and the job fails closed with a public reason code. No retry with backoff on provider errors yet | ADR 0005 |
| Trace and evaluate at every level | 🟢 Built. Every step and tool call is traced and in the evidence chain; components, workflow and end result are each evaluated | [evals](../evals/README.md) |
| Retrieved content is untrusted | 🟢 Built. Documents are screened for injection at intake; prompts treat inputs as data; drafts are checked for echoed instructions | [ADR 0004](adr/0004-guardrail-placement.md), [threat model](threat-model.md) |

---

## 5. Build, buy and operate

Two questions: what to build versus buy or use as a managed service, and who runs it over its life.

**Build or buy**

- **Build what differentiates you:** domain workflows, orchestration logic, how proprietary data is used, evaluation sets, governance controls.
- **Rent the commodity pieces:** model hosting, vector databases, identity, observability, content-safety services.
- **A thin custom layer on managed services.** You own the API contract, the orchestration, the evaluations and the controls; the cloud provider runs the infrastructure.
- **Plan the exit.** Abstraction layers, portable evaluation sets and owning your data let you switch vendors.

| Lean toward building | Lean toward buying or a managed service |
|---|---|
| A core differentiator | A commodity capability |
| Unusual compliance or control needs | Standard needs the vendor already certifies |
| A capable team in place | Time to value matters most |
| Stable, high volume | Requirements still changing |

**Operating**

- **Running it costs more than building it**, over its life: on-call, model upgrades, revalidation, keeping evaluations current, security patches. Budget for it from the start.
- **Decide how it will be run on day one:** by the client (which needs knowledge transfer), co-managed, or as a managed service. Each needs service-level targets, runbooks, monitoring and a clear chart of who owns what.
- **The firm stays accountable for vendor models.** Third-party risk management, concentration risk and validation still apply. This project applies SR 26-2's principles by analogy, since that guidance excludes generative AI from its scope ([ADR 0012](adr/0012-regulatory-position.md)).

**In Axiom today**

| Principle | Status | Where |
|---|---|---|
| Build the differentiators | 🟢 Built. The workflow, the router's policy, the guardrails, the evaluation sets and the evidence chain are this repo's own | [architecture](architecture.md) |
| Rent the commodity pieces | 🟡 Partly. Hosted models, Postgres, Phoenix and MLflow are standard components; locally they run in Docker. Managed services arrive with the week 9 cloud deployment | [dev plan](dev-plan.md) |
| Thin layer, own the contract | 🟢 Built. A thin in-house router rather than a large gateway library, chosen so sensitivity rules and evidence recording stay ours | [ADR 0005](adr/0005-model-routing.md) |
| Plan the exit | 🟢 Built. Model aliases, portable prompts, and evaluation sets that run on any provider; recordings let the demo run with no provider at all | |
| Budget for operation; define the run model | ⬜ Not yet. No service-level targets, runbooks or ownership chart. Due with the cloud deployment and before a second app | |
| Accountability for vendor models | 🟡 Partly. Each model call records alias, provider and prompt version for audit; no model inventory or vendor risk record yet | ADR 0012 |

## 6. Data for AI applications

Two things change. AI speeds up the data preparation teams already do. And generative AI apps need a new kind of preparation: turning unstructured knowledge into context a model can use reliably, where traditional machine learning turned structured data into features.

**AI speeds up traditional preparation**

- **Exploration:** a model with a code-execution tool can profile a dataset, write the pandas or SQL, plot distributions and flag anomalies in minutes.
- **Cleaning:** standardizing messy text fields (company names, addresses, free-text categories), suggesting data-quality rules, writing transformation code.
- **Labeling:** a model pre-labels text, and people check a sample.
- **Documentation:** drafting data dictionaries and inferring what columns mean.

**Have the AI write and run code; don't let it produce numbers itself.** A model that "reads" a table and reports a mean can make one up. Code gives exact, reproducible results, and someone still has to judge whether the findings make sense. AI speeds up the analyst; it doesn't replace the judgment.

**What's new: preparing knowledge for generative AI**

| Area | What it involves |
|---|---|
| Document parsing | Getting clean text from PDFs, scans, tables and slides. Tables and layout are where most retrieval failures start. |
| Chunking | Splitting documents along meaningful boundaries (sections, clauses) instead of fixed lengths, so each chunk stands on its own. |
| Metadata | Tagging each chunk with source, date, version, document type and owner, so retrieval can filter and answers can cite sources. |
| Deduplication and versioning | Removing superseded versions and drafts. If two versions of a credit policy are both indexed, the model may cite the old one. |
| Freshness | Re-indexing pipelines, so the knowledge base doesn't go stale. |
| Permissions | Carrying each document's access rights into the index, so retrieval never shows a user something they couldn't open themselves. A major risk in banks. |
| Personal and sensitive data | Masking or redacting before indexing or sending to a model, with tools such as Presidio or a data classification service. |
| Semantic layer | For agents that query structured data: business definitions ("what counts as revenue"), so a text-to-SQL agent doesn't guess. |
| Evaluation datasets | Golden question-and-answer sets with the expected sources. The generative AI version of a test set, and the step many teams skip. |
| Synthetic data | Generating realistic test cases and edge cases, with checks that the data is realistic. |

**Treat the knowledge base as a product with an owner.** Parsing, versions, freshness and permissions decide what the model can say; they need the same care as the model choice.

**In Axiom today**

| Principle | Status | Where |
|---|---|---|
| Code produces the numbers | 🟢 Built. Models extract figures, and code checks each one against its cited source line and fiscal-year column; code computes every ratio, reconciles the statements and applies the policy thresholds. A model never reports a number it computed | `src/axiom/apps/credit_memo/agents/intake.py`, `src/axiom/apps/credit_memo/tools/` |
| AI-assisted labeling, checked by people | 🟡 Partly. The golden answers are derived by code from the policy text and are a draft until reviewed; the grader's calibration set has human labels in part | [golden answers](../evals/golden/v1/answers.md), [evaluation suite](../evals/README.md) |
| Document parsing | ⬜ Not yet needed. Every document is clean, generated Markdown. Real packages arrive as PDFs and scans, so a parser, with checks on tables, is needed before real data | [synthetic data](../data/synthetic/README.md) |
| Chunking along meaningful boundaries | 🟡 Partly. The in-memory index splits on paragraphs, and a statement table stays one chunk; GKN chunks its own way. Nothing splits by section or clause yet | `src/axiom/mcp_clients/evidence.py` |
| Metadata for filtering and citation | 🟡 Partly. Every chunk carries its collection, document id, chunk id and a fingerprint, and every claim cites a source line. No date, version or owner on the chunk | `src/axiom/mcp_clients/evidence.py` |
| Deduplication and versioning | 🟡 Partly. One credit policy version (CP-2026.2) is indexed, and Regulus serves pinned snapshots. Nothing yet stops two versions being indexed together | [credit policy](../data/synthetic/credit_policy/README.md) |
| Freshness | 🟡 Partly. A job's documents are indexed when it starts and deleted with the job. The reference documents are fixed snapshots, updated by hand; no re-indexing pipeline | |
| Permissions carried into retrieval | 🟡 Partly. Retrieval is scoped to the job, which the platform sets and a model can never choose, so one job never sees another's documents; role keys decide who reads what. No per-document access rights yet, which a second app with shared documents will need | `src/axiom/mcp_clients/evidence.py`, [threat model](threat-model.md) |
| Mask personal data before indexing and model calls | 🟢 Built. Presidio masking runs first; the index and every model see only masked documents, and financial values stay unchanged. If masking is off or fails, every model call is treated as unmasked, and only a local model may take it | `src/axiom/apps/credit_memo/graph.py`, [ADR 0005](adr/0005-model-routing.md) |
| Semantic layer: business definitions | 🟢 Built for this app's scope. Each ratio's formula is defined once in code and each threshold once in config, and "Explain this" shows each formula with its inputs and the policy clause behind each flag. No agent writes queries, so there is no text-to-SQL layer to define | `src/axiom/apps/credit_memo/tools/ratios.py`, [`policy_thresholds.yaml`](../config/policy_thresholds.yaml) |
| Evaluation datasets with expected sources | 🟡 Partly. The golden set holds every figure's value and source line, the expected flags, recommendations and key findings. No retrieval set yet that scores whether the right passages came back | [golden set](../evals/golden/v1/answers.md) |
| Realistic synthetic data | 🟡 Partly. Numbers are generated first and text second, so every figure has a known answer, and the expected ratios are computed by a second implementation that must agree with the platform's. Boundary cases are tuned to land exactly on each limit. No check against the distributions of real loan packages | [synthetic data](../data/synthetic/README.md) |

---

## Change log

| Date | Change |
|---|---|
| Nov 15, 2026 | First version: five areas, with the credit memo app checked against each |
| Nov 15, 2026 | Anthropic and Ollama adapters added; providers can be mixed by alias |
| Nov 15, 2026 | New section 6, data for AI applications |
