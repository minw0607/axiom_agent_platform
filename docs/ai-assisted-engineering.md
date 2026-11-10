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
