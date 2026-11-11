# AI-assisted engineering log

What was delegated to Claude Code, what was changed after review, and what was learned. One entry per week of the [development plan](dev-plan.md).

## Week 2 (Nov 3): supporting services

**Delegated.** The MCP servers for GKN (`ingest_documents`, `retrieve_evidence`, `delete_job`) and Regulus (`lookup_provisions`, `recommend_mitigants`), with Dockerfiles, tests, the platform `docker-compose.yml`, an authenticated MCP client, a Gate 1 check script, and ADRs 0004–0005.

**Found during the work.**
- The MCP Python SDK is now version 2. `FastMCP` was renamed `MCPServer`, and the client takes a transport object to send auth headers. Code written from version 1 examples would not import.
- Regulus had no committed EU AI Act snapshot, so a fresh container would have downloaded it from EUR-Lex at startup. A snapshot and an offline mode now keep the service keyless and network-free.
- Regulus finds its data relative to its source tree, so its image uses an editable install.
- The Regulus README said committed snapshots held only structure and titles. That was already untrue for NIST and OECD and has been corrected.
- An unbounded per-query retrieval cache in Regulus would grow without limit in a long-running service. The service now caps it.
- GKN isolation is structural: each collection has its own index, and a query only reads the one it asks for. There is nothing to filter after scoring.

**Verified.**
- Unit and contract tests: GKN 25, Regulus 54, platform 16.
- Both servers were called over real HTTP. A missing secret returns 401 and a spoofed Host header returns 400.
- `scripts/check_gate1.py` was run against both services started natively. Six of seven checks passed; Phoenix needs Docker.

**Gate 1 (Nov 9).** With Docker installed, `docker compose up -d --build` built both MCP images from their `v0.2.0` tags and started all five services; `scripts/check_gate1.py` passed 7 of 7. The collector config used the deprecated `otlp` exporter alias and now uses `otlp_grpc`.

**Threat model v0 (Nov 10).** Delegated: the first draft of `docs/threat-model.md` from plan sections 8.2, 8.4 and 10.2.
- The OWASP LLM Top 10 names were checked against the Regulus snapshot. The agentic Top 10 names were checked against a published summary, because no local source had them.
- Every control marked Built was checked against the code. One claim was corrected: the MCP images pin Python to `3.11-slim`, not to an exact version or digest.
- Two agentic items, code execution and inter-agent messaging, are marked not applicable rather than given placeholder controls, with the reason stated.

**Architecture and ADRs 0001–0003, 0012 (Nov 10).** Delegated: first drafts of `docs/architecture.md` and four ADRs, from plan sections 2–8 and 13.
- The SR 26-2 position was checked against the Federal Reserve's PDF, as the week 1 plan required. The fetch tool returned the PDF unreadable, so its text was extracted locally. Two points confirmed: footnote 3 places generative and agentic AI outside the guidance's scope, and the definition of "model" excludes deterministic rule-based processes. So neither the agents nor the ratio code is a "model" under SR 26-2, which supports applying it only by analogy.
- One ADR sentence implied that keeping tools out of MCP was what stopped the model doing arithmetic. It was reworded: the arithmetic is in tested code, and the model only receives results.

**Review fixes (Nov 10).** Two problems found by the author's review, not by the AI:
- **Outdated OWASP edition.** The threat model cited the OWASP LLM Top 10 2025, but the 2026 edition was released Aug 3, 2026. The AI had used the copy in the Regulus snapshot, which is the 2025 list. The 2026 names were confirmed from OWASP's official PDF. Eight of ten entries moved, and System Prompt Leakage became Hidden Context Exposure, so every threat was remapped. The same PDF independently confirms five of the agentic list names.
- **Broken diagram.** The architecture sequence diagram did not render on GitHub. Two arrow labels contained semicolons, which Mermaid reads as the end of a statement. All four diagrams in the docs now parse with Mermaid 11.4.1 in a browser.

Lesson: confirm that a framework version is current at the source, not just that its names are right. Render every diagram before publishing.

**Regulus v0.2.1 (Nov 11).** Delegated to a separate session: updating Regulus's OWASP LLM snapshot from the 2025 edition to the 2026 edition, using the official v1.0 PDF. Regulus's 55 tests pass and its retrieval metrics match the 2025 baseline. The platform now builds regulus-mcp from `v0.2.1`, and `scripts/check_gate1.py` was re-run against the rebuilt image.

**Week 3 vertical slice (Nov 11).** Delegated: the synthetic data generator, ratio, policy and reconciliation tools, router v1, the three agents and the LangGraph workflow, the job store, API and worker, the platform Dockerfile, and ADRs 0007 and 0011. What the checks caught:
- **A two-cent disagreement on debt service.** The hand-worked test expected 231,993.60; the code gave 231,993.62. The code had annualized an unrounded monthly payment. Lenders bill whole cents, so both the tool and the generator now round the monthly payment first. The convention is recorded in ADR 0007 and in each result's formula.
- **A bug only a real database shows.** The advisory-lock key used a NUL separator, which Postgres text cannot hold. The in-memory store accepted it; the integration test against Postgres failed at once. CI now runs Postgres as a service so this class of bug cannot slip through.
- **Scenario tuning.** The first generated packages tripped extra flags (the weak-DSCR borrower also breached leverage). Parameters were tuned until each edge-case package raises only the flag it was built for, and a test pins that.
- **Demo responses are labeled.** Keyless demo mode needs stored responses. Seeding them by hand would have looked like model output, so `scripts/seed_recordings.py` runs the real workflow with a scripted stand-in and marks every file `origin: scripted`. Each one passes the same validation a model's output must pass.
- Several libraries were newer than the assistant's working knowledge (OpenAI SDK 3.x, LangGraph 1.2, FastAPI 0.162), so their APIs were checked by introspection before use. The OpenAI adapter has not yet run against a live key.

**First live run (Nov 11).** Delegated: Azure OpenAI and API-gateway support in the OpenAI adapter, copying the gateway settings into the local `.env` without displaying them, and a live run of one package.
- **The scripted recordings hid a prompt gap.** For a healthy borrower with no risk flags, the live model left "Risk factors" empty. The validator rejected the draft twice and the job failed closed, as designed. The drafter v1 prompt never said what to write when nothing is wrong; the scripted response had filled that in on its own. `drafter/v2` requires a cited claim in every section. v1 is kept so older evidence still matches its prompt hash. With v2 the run completed on first attempts, and every extracted figure matched ground truth.
- **A gap in the evidence trail.** When the job failed, the drafter's rejected attempts were not in the call log. Week 4's evidence log must record every attempt, failed ones included.
- **iCloud offloading.** The repo sits in an iCloud-synced folder, and files in `.venv` had been offloaded. A 1.5-second test run took 21 minutes, mypy's cache broke, and the editable-install `.pth` file was hidden again. The virtual environment should live outside the synced folder.

**Live recordings for all ten packages (Nov 11).** Delegated: recording real responses for the demo. Each run went to a staging folder first, so the demo was never left half replaced. It took three rounds:
- **Round 1 (intake v1).** Every figure the model returned was correct, but in five packages whole fiscal years were missing, and in one a row worded "Repayment of long-term debt" was skipped. Intake checked each figure, never that the set was complete, so jobs carried on with gaps. Code now rejects an extraction that leaves out a year or a row, and the error goes back to the model in the retry. `intake/v2` asks for every year and for rows worded differently from their field.
- **Round 2 (intake v2).** All 552 figures were correct. Two recommendations differed from the expected result. The author decided both (Nov 11):
  - **A DSCR floor.** pkg-04's pro forma DSCR is 0.93, and the model chose approve-with-conditions, which CP-6.1 allowed as an exception. Policy CP-2026.2 adds CP-6.3: below 1.00x a request is declined. Code raises `DSCR_BELOW_DECLINE_FLOOR` and the validator requires "decline".
  - **Concentration measured by code.** pkg-08's draft stated that the largest customer is 48% of revenue, then recommended "approve", against CP-5.2. Concentration was only a sentence in the business description. Intake now extracts it as a sourced figure, and the policy tool flags it above 25% (CP-5.1) and 40% (CP-5.2). If the figure is missing, a high-severity flag is raised.
- **Round 3 (drafter v3).** The first wording of v3 said "approve" was allowed when no high-severity flag was raised. Two packages with only warning flags came back "approve", where the round 2 drafts had used conditions. The prompt and the validator now agree that any risk flag blocks a plain "approve". Final result: all ten recommendations match, all 561 figures match ground truth on first attempts, and the demo replays these recordings with matching prompt hashes.
- **Tests no longer depend on a model's judgment.** The scripted responses moved to `tests/fixtures/recorded/` and the tests replay those. The demo replays the live ones.

Lesson: a validator that checks each answer is not the same as one that checks the whole answer. Scripted data hid both gaps, because it was built from the ground truth and so was always complete. Only the live model found them.

**Evidence log (Nov 11, week 4).** Delegated: the append-only evidence log and the auditor-only evidence endpoint.
- Each job has its own SHA-256 chain: an event's hash covers its content and the previous hash. Postgres triggers reject UPDATE, DELETE and TRUNCATE on the table, so append-only does not rest on application code alone. The submission event is written in the same transaction as the job.
- The gap found by the first live run is closed. A per-node collector records each model call as it is made, so a node that fails keeps its rejected attempts. Before, they were lost with the exception.
- Two choices worth stating: events are written after the run rather than one by one, so a worker crash mid-run loses that run's events (the week 4 checkpointer revisits this); and a chain truncated at the end still verifies, so completeness depends on the terminal event (`job_awaiting_review` or `job_failed`) being present. A test records that limit.
- **The evidence log found a demo bug on its first run.** A pkg-04 job in the Docker stack failed with `model_unavailable`. The log showed why: the drafter's first attempt was rejected, and the retry had no recording. The live recordings had been made with in-memory retrieval, which names passages `doc#1`; the stack retrieves through GKN, which names them `doc_chunk_0`. The validator rightly rejected citations to passages that were never retrieved. All ten packages were re-recorded against GKN, and each now reaches `awaiting_review` through the stack with a verified chain. Gate 2's demo check should replay every package through the stack, not in-process.
