# ADR 0003: Tool integration

**Status:** Accepted (week 2). The shared secret is replaced by a tool gateway with per-caller tokens and a secret per service in [ADR 0014](0014-tool-gateway.md) (week 7).

## Context

The agents use two kinds of tools:

- **External knowledge services** built in separate repositories: job-scoped evidence retrieval (geometric_knowledge_network) and AI governance lookup (Regulus). They have their own release cycles, dependencies and audiences.
- **Deterministic tools** inside the platform: ratio calculations, trend analysis and credit policy threshold rules. Their results must be exact and are covered by the 100% ratio exactness gate.

The integration must keep the sibling repos independent (the platform depends on them, never the reverse), authenticate every call, limit what each caller can reach, and stay keyless in demo mode.

## Options considered

| Option | Strengths | Weaknesses |
|---|---|---|
| MCP servers | Standard protocol for model tools; tool schemas discovered at runtime; each service runs and is versioned on its own; same tools usable by other MCP clients | Network hop and a running process per service; protocol and SDK still changing; needs its own authentication |
| Direct function calls (import the library) | Fastest; no network; simple to test | Couples the platform to each repo's dependencies and Python version; services cannot be scaled or secured separately |
| Custom REST tools | Familiar; full control over the contract | A bespoke client and schema per service; reinvents what MCP standardizes |

## Decision

Use a **split by tool type**:

- **MCP over streamable HTTP for the external knowledge services.** regulus-mcp exposes `lookup_provisions` and `recommend_mitigants`. gkn-mcp exposes `ingest_documents`, `retrieve_evidence` and `delete_job`. Each runs in its own container, built from a pinned release tag, and requires a shared bearer secret.
- **Direct, in-process function calls for deterministic tools.** Ratio and policy-rule functions live in `src/axiom/tools/` and are plain, unit-tested Python. A network boundary would add failure modes and nothing else.

The platform calls MCP services through one client, `MCPToolClient` in `src/axiom/mcp_clients/`. Scope-defining arguments such as `job_id` and `collection` are set by platform code, never by model output.

## Consequences

- GKN and Regulus stay standalone. Their dependencies cannot conflict with the platform's, and each can be tested and released alone.
- The services enforce their own boundaries: job isolation in GKN, read-only reference collections, input limits, host checking and authentication. These are tested in each repo and in `scripts/check_gate1.py`.
- One shared secret covers both services. Least privilege per agent is enforced in the platform's client, not by the services (threat model T-03, T-05).
- The MCP Python SDK moved to version 2 during week 2 with breaking renames. SDK upgrades are pinned and go through CI.
- Arithmetic runs in tested platform code. Models receive the results; they never compute them.
