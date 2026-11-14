# ADR 0014: MCP tool gateway

**Status:** Accepted (week 7, Nov 14, 2026)

## Context

ADR 0003 put the two external knowledge services (GKN and Regulus) behind MCP, with one shared bearer secret. By week 7 that left three gaps, recorded as residual risk in the threat model:

- **One secret opened everything.** The worker held a secret that let any code path call any tool on either service, including `delete_job`. A leak of that secret opened both services (T-03).
- **No per-step limits.** The design said only intake should ingest documents and only finalize should look up governance guidance, but nothing enforced it. Scope arguments were set by code, yet a bug or a model-steered code path could still call a tool its step never needs (T-05, OWASP LLM03:2026 Excessive Agency, ASI02, ASI03).
- **Tool calls were not in the evidence chain.** The bundle recorded every model call but no tool call, so an auditor could not see which step reached which service.

Four workflow steps call tools, and every call has a fixed shape:

| Step | Tool | Scope |
|---|---|---|
| intake | `ingest_documents` | this job |
| drafter | `retrieve_evidence` | collection `job` |
| compliance | `retrieve_evidence` | collection `lending_refs` |
| finalize | `lookup_provisions` | none |

No step deletes a job; only maintenance scripts do. `recommend_mitigants` is never used.

## Options considered

| Option | Strengths | Weaknesses |
|---|---|---|
| Keep the shared secret; enforce limits in the client | No new service | The limit lives in the process it is meant to limit; still one secret for both services; no central log |
| A separate secret per service, no gateway | Small change | Each step still holds every tool of a service; no per-step limits; no central log |
| **An MCP gateway in front of both services** | One entry point; per-caller tokens; rules in one reviewed file; one log of every call; the services' secrets leave the worker | One more container and network hop; the gateway itself must be trusted |
| A per-step process, each with its own credential | Limits survive a compromised worker | Splits the LangGraph workflow across processes; far larger than the demo needs |

## Decision

Add **`tool-gateway`**, a small MCP server in this repository (`src/axiom/gateway/`), as the only route from the platform to its tool services.

- **Per-caller tokens.** Each of the four steps, and an `operator` caller for maintenance scripts, has its own bearer token. The worker sets the caller around each node (`acting_as`, the same way it collects model calls); a model never chooses it. The worker holds the four step tokens only: never the operator token or a service secret.
- **Rules in `config/tool_gateway.yaml`.** A call is allowed only if the caller lists the tool and every named argument takes an allowed value (for example the drafter may retrieve only from `collection: job`). A missing argument is refused, so the rules fail closed. A tool no caller lists is not offered.
- **A secret per service, held only by the gateway.** GKN and Regulus each get their own secret. They no longer publish ports; only the gateway (127.0.0.1:8003) is reachable from the host.
- **One log of every call.** Each call, allowed, refused or failed, is appended to a hash-chained JSON lines log in the `gateway-data` volume, with the caller, tool, decision, a call id and fingerprints (SHA-256) of the arguments and result, never their text. The gateway returns the call id, and the worker records each call as a `tool_call` event in the memo's evidence chain, so the auditor's trail shows it beside the model calls.
- **Keyless demo unchanged.** `demo-keys` generates the service secrets and tokens. `docker/with-demo-keys.sh` hands each container only the values named in its `AXIOM_DEMO_KEYS`. A value set in `.env` still wins.

Local development without Docker can still call a service directly with `MCP_SHARED_SECRET`, or use in-memory retrieval.

## Consequences

- A refused call never reaches a service, and it is visible twice: in the gateway's log and in the memo's evidence chain. Gate 1 now checks the refusals and that the services are unreachable from the host. Gate 2 checks that every tool call in every package came through the gateway from the step that owns the tool.
- A leaked step token reaches one tool, within its allowed scope. A leaked service secret is useless outside the stack's network.
- **Limit:** all four steps run in one worker process, which holds all four step tokens. The tokens stop a bug or a model-steered code path from calling the wrong tool. They do not stop someone who controls the worker process. Closing that gap needs a process per step; it is not planned.
- The gateway does not check that `job_id` belongs to the running job; the platform sets it from graph state (ADR 0003). The gateway forwards tool results unchanged, so results still pass through the platform's own checks as before (T-03).
- Every tool call adds one network hop inside the stack. Gate 2's 25 packages ran end to end in 86 seconds, about the same as before.
