# ADR 0002: API interaction model

**Status:** Accepted (week 2)

## Context

A memo job runs several model calls, tool calls and checks, which takes from tens of seconds to minutes. It then waits for a human reviewer, which can take days. Consumers (a loan origination system, the review page, scripts) need to submit work, learn when a draft is ready, record a decision, and retrieve evidence. Submissions must be safe to retry, because clients and networks fail.

## Options considered

| Option | Strengths | Weaknesses |
|---|---|---|
| Synchronous request and response | Simplest client | Requests would hang for minutes and time out at proxies; cannot represent a multi-day human pause; a retry after a timeout would start a duplicate job |
| Async job with polling | Submission returns at once; job state is explicit and queryable; works through any proxy; easy to test | Clients poll; slight delay between completion and notice |
| Async job with webhooks | Clients are notified without polling | Needs callback registration, signed payloads, retries, and SSRF protection (threat model T-15); clients must expose an endpoint |

## Decision

Use an **async job with polling** for the MVP. Add **webhooks in phase 4** as an optional notification on top of the same job model.

- `POST /v1/memos` validates the package, stores the job and returns `202 Accepted` with `job_id` and a `Location` header. A worker runs the graph.
- `GET /v1/memos/{job_id}` returns the state (`queued`, `running`, `awaiting_review`, `revising`, `approved`, `rejected`, `failed`) and, when ready, the draft.
- `POST /v1/memos/{job_id}/review` records the decision. The reviewer comes from the authenticated key, and the submitter cannot approve.
- An `Idempotency-Key` header is required on submission. The same key and body return the stored response; the same key with a different body returns 422.
- Errors use RFC 9457 problem+json.

## Consequences

- The API never holds a connection open while models run, so timeouts and retries are simple.
- The job state machine is the single source of truth for clients, the review page and the evidence log.
- Retried submissions cannot create duplicate jobs or duplicate model spend.
- Webhooks can be added later without changing existing endpoints: they notify about the same state changes clients can already poll.
- The SSRF risk that comes with webhooks is deferred with them, not ignored.
