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
