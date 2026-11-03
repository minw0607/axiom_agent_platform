# ADR 0005: Model routing approach

**Status:** Accepted (week 2)

## Context

The platform calls models from several providers: a hosted frontier model, a second hosted family for judges and fallback, a small local model, and recorded responses for the keyless demo. The routing layer has to:

1. Resolve logical aliases (`small-a`, `frontier-b`) to real deployments from environment settings, so the repo and published results never name a deployment.
2. Route by task and by data sensitivity. Unredacted PII may only go to a permitted local model, and if none is available the job must stop (fail closed).
3. Fall back to a second model when the primary fails.
4. Record the routing decision (alias, provider, reason, fallback used) in the evidence bundle and on the trace.
5. Support shadow and canary assignment in phase 4.

## Options considered

| Option | Strengths | Weaknesses |
|---|---|---|
| Single provider | Simplest; one SDK | No fallback; no local model for sensitive content; no cross-family judges |
| LiteLLM | Normalizes many providers; built-in retries and fallbacks | Large dependency; its routing does not express sensitivity rules or fail-closed behavior, so that policy would still be ours; harder to guarantee deployment names stay out of logs |
| Thin in-house adapter | Small, fully typed; policy, aliasing and evidence recording in one place; recorded-response mode is just another adapter | We maintain one adapter per provider (four in scope) |

## Decision

Build a **thin in-house router** in `src/axiom/router/`:

- A `ModelAdapter` protocol with one method for chat completion that returns text, token counts and latency. Adapters: Azure OpenAI, Anthropic, Ollama, and `recorded` (demo mode).
- A `ModelRouter` that loads `config/routing.{demo,local,cloud}.yaml`, applies sensitivity rules first, then the task route, then the fallback, and returns a `RoutingDecision` together with the response.
- Alias-to-deployment mapping from `MODEL_ALIAS_MAP` in the environment only.

LiteLLM stays an option *behind* the adapter interface. If provider count grows, one adapter can wrap it without changing callers or the policy.

## Consequences

- Sensitivity routing and fail-closed behavior are unit-testable without network calls, using the recorded adapter.
- Every model call produces a `RoutingDecision` for the evidence bundle and an OpenTelemetry span with alias, tokens, cost and latency. The real deployment name is not recorded in published output.
- Adding a provider means writing one adapter and its contract test.
- Retries and backoff are our code. They stay simple: one retry, then fall back.
