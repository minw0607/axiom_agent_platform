# ADR 0016: The contract between the platform core and an app

**Status:** Proposed (Nov 18, 2026). Draws the boundary that [ADR 0015](0015-platform-core-and-apps.md) deferred, now that a second app is chosen. No code has moved yet.

## Context

ADR 0015 set the direction: a platform **core** with **apps** on top, restructured once a second app is real. The second app is now chosen: **model documentation review**. A model owner submits a model's documentation. The app checks it against required sections and governance frameworks, drafts a cited findings report, and stops for a validator, who approves, rejects or asks for a revision. It is close enough to the credit memo to share every control, and far enough from lending to show where the boundary really is.

| | Credit memo (app 1) | Model documentation review (app 2) |
|---|---|---|
| Submitter | Relationship manager | Model owner |
| Reviewer | Credit officer | Model validator |
| Input | Borrower package: statements, application, business description | Documentation package: development report, data description, monitoring plan |
| Deterministic tools | Ratios, reconciliation, credit policy rules | Required-section checklist, completeness score |
| Retrieval | Job documents, lending references | Job documents, governance provisions (Regulus) |
| Result | Credit memo with cited claims | Findings report with cited findings |
| Decision the app must never state | "The loan is approved" | "The model is approved" or "validated" |

Five core packages still import lending code (table in ADR 0015): the worker calls the lending workflow and catalog, the API serves `/v1/memos` and loads borrower packages, guardrails check `MemoDraft` and `FinancialStatements`, tool clients and checkpoints use lending and agent types.

Regulus covers the EU AI Act, NIST AI RMF and its GenAI profile, OECD, ISO/IEC 42001 (structure only), MITRE ATLAS and OWASP LLM. It does not hold US supervisory model risk guidance. App 2 uses what Regulus has, plus its own section checklist drawn from public frameworks. Adding a framework to Regulus is separate work in the Regulus repository.

## Options considered

**How apps live in the code**

| Option | Strengths | Weaknesses |
|---|---|---|
| A service per app, each with its own API and worker | Strong isolation; apps deploy on their own | Every control runs in every service, so it can drift (the problem ADR 0015 set out to avoid); several times the containers |
| **App packages in one repository, registered with the core** | One worker, one API, one copy of each control; an app is a Python package; a test enforces the boundary | Apps share a release; a broken app can fail the shared test run |
| A generic workflow engine, apps written as configuration only | No code per app | Agents, tools and result schemas don't fit in YAML; the engine becomes the hardest part of the platform |

**How the API serves several apps**

| Option | Strengths | Weaknesses |
|---|---|---|
| **A route per app: `/v1/apps/{app_id}/jobs`, with `/v1/memos` kept as the credit memo's route** | Existing clients don't change; each app has its own result schema in OpenAPI | Two paths reach credit memo jobs |
| One `/v1/jobs` with an `app` field | One set of routes | A result field that changes shape by app; weaker OpenAPI |
| A new API version, `/v2` | A clean break | Breaks the contract ("the API is the contract") for no user benefit |

## Decision

**App packages in one repository, and a route per app.**

### 1. Where code lives

- Core packages stay where they are: `api/`, `jobs/`, `evidence/`, `gateway/`, `guardrails/`, `mcp_clients/`, `memory/`, `router/`, `telemetry/`.
- Each app is a package under `src/axiom/apps/<app_id>/`, with its own `agents/`, `graph.py`, `domain.py`, `tools/`, `catalog.py` and `prompts/`. The credit memo app is `apps/credit_memo/`; app 2 is `apps/model_review/`.
- **The boundary is a test.** `tests/test_boundary.py` fails if any core module imports `axiom.apps`, so rule 1 of ADR 0015 stops being a convention.

### 2. What the core defines

Generic types move out of the lending code into a core module, `axiom/core/types.py`:

- `Document`, the line-addressed source text every app reads (today in `domain.py`);
- `Claim` and `Citation`, a statement and the document lines or retrieved passages behind it. `MemoDraft` becomes a credit memo specialization of a generic `Draft` (a list of claims);
- `CheckResult`, `ModelCall`, `ToolCall` and `ReviewDecision`;
- `CoreState`, the fields every workflow state carries: `job_id`, `documents`, `source_hashes`, `model_calls`, `tool_calls`, `guardrails`, `reviews`, `human_revisions`, `completed`, `outcome`, `error`, `error_node`. Each app's state extends it (`MemoState` adds financials, ratios and findings).

The core keeps doing what every app needs:

- the job queue, checkpoints, the pause for review, and separation of duties;
- the hash-chained evidence log and the evidence bundle;
- the tool gateway and its policy;
- the guardrail suite: masking personal data, the injection screen, and checks that every claim is cited and every citation resolves;
- model routing, recordings and traces;
- the daily job cap;
- the evaluation harness and gates.

### 3. What an app provides

An app registers one object that meets the `App` protocol in `axiom/core/app.py`. The table is the protocol as built in step 1b; it replaced the shorter list first proposed here, because the worker and API need more than a graph and a result to stay free of app code.

| Member | What it is | Credit memo |
|---|---|---|
| `app_id`, `title`, `noun` | A stable ID, a display name, and what a job produces in API messages | `credit_memo`, "Credit memo", "memo" |
| `roles` | Display names for the three core roles (submitter, reviewer, auditor) | Relationship manager, credit officer, auditor |
| `inputs`, `load_input`, `has_recordings` | The catalog: lists inputs, loads one (raising `UnknownInput`), says whether demo mode can replay it | The borrower packages in `data/synthetic/` |
| `build_context`, `started` | What the workflow needs to run, and the versions recorded when a job starts | Router, tools, policy thresholds, prompts, guardrails; policy and prompt versions |
| `run`, `resume`, `paused_for_review` | Run a new job until it pauses, resume it with a review decision (raising `ReviewError`), and tell whether it is paused | `graph.py` |
| `serialize`, `node_outputs`, `node_details` | The stored result, and what each node's `node_completed` event hashes and adds | The memo state; risk flags, draft recommendation, triggered rules |
| `request_model`, `job_model`, `to_public` | The request body and the public job, Pydantic models that appear in OpenAPI | `MemoRequest`, `MemoJob` |
| `evidence_model`, `to_evidence` | The evidence bundle | `EvidenceBundle`, with compliance findings |
| `error_codes` | The app's own exceptions and their public failure reasons, added to the core's | `IntakeError` is `invalid_package` |
| `max_revisions` | How many times a reviewer may ask for a revision | 2 |
| `state_modules`, `legacy_modules` | Types allowed in a checkpoint, and their module names before a move | `domain`; `axiom.domain` |
| `legacy_route`, `extra_routes`, `source_description` | A route kept from before this ADR, routes outside the job routes, and the source route's description | `/v1/memos`; `/v1/packages`, `/v1/policy/{ref}` |

What the output checks compare a draft with (`draft_facts`) stays inside the app: the app builds it and hands it to the core guardrails when it calls them.

Configuration and data are grouped by app so that one app's settings can't change another's:

- **Prompts:** `src/axiom/apps/<app_id>/prompts/`, versioned as today.
- **Recordings:** `data/recorded/<app_id>/`. A recording is keyed by the SHA-256 of its request, so moving files changes nothing as long as each prompt's text is unchanged.
- **Gateway policy:** callers are named `<app_id>.<step>` (for example `credit_memo.drafter`) in `config/tool_gateway.yaml`, each with its own token.
- **Guardrails:** `config/guardrails.yaml` gets a section per app, choosing which controls run and with what settings. The decision-language check keeps its logic in the core and takes its phrases from the app ("approve the loan", "the model is validated").
- **Thresholds and gates:** `config/apps/<app_id>/` for app thresholds; `config/eval_gates.yaml` gets a section per app.
- **Evaluation sets:** `evals/<app_id>/`, with the golden set, judge calibration and adversarial cases per app. The runner and scoring stay shared.

`config/apps.yaml` lists the enabled apps by import path. The worker and the API load only those.

### 4. Jobs, evidence and the API

- **Jobs** get an `app_id` column (`NOT NULL`, default `credit_memo` for existing rows). `package_id` keeps its name and means the input's catalog ID in any app.
- **Evidence:** the `submitted` event gains `app_id`. Events already written keep their hashes and still verify; only new events carry the field.
- **API:**
  - `GET /v1/apps` lists the enabled apps and their roles.
  - `/v1/apps/{app_id}/jobs` gives each app the same routes `/v1/memos` has today: submit, list, get, source, review, evidence.
  - `/v1/memos` stays as the credit memo's route, with the same schemas, so the contract tests don't change.
- **Role keys** stay platform-wide in the restructure: today's keys work for both apps, and separation of duties is checked per job, as now. Granting a key a role in one app only is added if app 2 needs it, as a `roles` entry per key in `config/apps.yaml`.
- **The daily cap** stays platform-wide, so the total cost limit holds however many apps there are.
- **The review page and dashboard** keep their layout from the core and take an app's sections from that app's templates. "Explain this" stays a credit memo feature until app 2 needs its own.

### 5. Order of work and how each step is proved

1. **Restructure with no change in behaviour.** Move the credit memo code under `apps/credit_memo/`, move generic types into the core, route the worker and API through the registry, and add the boundary test. Proof that nothing changed:
   - every existing test passes, with test files moved but not edited beyond imports;
   - the boundary test passes;
   - the recorded demo replays every package with the same prompt hashes;
   - the golden demo results match the committed `evals/results/golden-demo.json`;
   - Gate 2 passes, 13 of 13, from a clean clone.
2. **Build app 2 keylessly**, with stub model responses in its tests: synthetic documentation packages, the section checklist in code, Regulus retrieval through the gateway, the findings draft, and the core guardrails with app 2's phrases.
3. **Evaluate and record app 2:** a small golden set, its gate thresholds, and recordings for the keyless demo. This needs live model runs; the token estimate is given and approved first.

### 6. Rules for app 2's content

- **All data synthetic.** Documentation packages are written for this repository and describe fictional models. Nothing comes from any real organization.
- **LLMs reason; code calculates.** Whether a required section is present, and the completeness score, come from code. The model judges whether a section is adequate and drafts findings, each citing the document line or provision behind it.
- **The validator decides.** The report recommends and lists findings; it never states that a model is approved, validated or fit for use. The decision-language check enforces this.
- **Regulus is a primary source here,** unlike in the credit memo, where it is related guidance only (ADR 0012). Each provision is cited with its framework and ID, and a finding that cites a provision is checked like any other citation.

## Consequences

- **The core-app boundary is enforced by a test,** not by review.
- **Existing clients see no change.** `/v1/memos`, its schemas and the role keys stay as they are.
- **One app's mistake can fail the shared test run.** This is accepted while there are only two apps: one release with all controls tested together is worth more than isolation.
- **Jobs paused before the move still resume.** A checkpoint names each stored object by its Python module, such as `axiom.domain.MemoDraft`. Without the old names, LangGraph's serializer does not fail: it quietly loads plain dictionaries, and the job breaks later, on resume. Each app therefore declares its old module names (`legacy_modules`), the checkpoint allowlist accepts them, and `tests/test_checkpoint_legacy.py` loads a checkpoint written under the old names.
- **The checkpoint allowlist comes from `config/apps.yaml`.** It must include each enabled app's state types without the core importing app code, so the core reads the enabled apps from configuration. This is the first piece of the app registry.
- **Recordings survive the move only if prompts don't change.** Step 1 must not edit prompt text; the replay check proves it didn't.
- **Some lending vocabulary stays in the API for now:** the `package_id` field and the `/v1/memos` route. Renaming them would break clients, so they stay.
- **Revisit when:** a third app arrives, an app needs its own release cycle or deployment, or the shared test run becomes a bottleneck. Each would point to the service-per-app option.
