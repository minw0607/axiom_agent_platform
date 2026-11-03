# ADR 0004: Guardrail placement

**Status:** Accepted (week 2)

## Context

Guardrails could sit in one place, at the API gateway, or in each graph node, or at several stages. The risks they address arise at different points:
- Prompt injection and PII arrive with the borrower's documents.
- Off-task or final-decision language appears during generation.
- Unsupported claims and invented numbers appear only in the finished draft.
- The approval requirement is a process control rather than a content control.

A gateway sees only the request and the final response, so it cannot inspect retrieved documents or intermediate drafts. Checks inside each node can see everything, but they scatter policy across the code and make each control hard to switch off for measurement.

## Options considered

| Option | Strengths | Weaknesses |
|---|---|---|
| Gateway only | One place to configure; easy to reason about | Cannot see uploaded documents after intake, retrieved evidence, or drafts; misses most of the threat model |
| Per-node | Each check sees exactly what its node handles | Policy spread through the code; hard to toggle or audit consistently |
| Layered | Each control runs at the stage where its risk appears; a shared interface keeps them consistent | More components; needs a common result format |

## Decision

Use **layered guardrails** at four stages, each control implementing one interface that returns `passed`, `triggered` or `not_evaluated`:

| Stage | Controls |
|---|---|
| Input (intake node, before indexing) | Presidio PII redaction; Prompt Shields on redacted documents |
| Generation (drafting nodes) | Policy rails: no final-decision language, no investment advice, stay on task |
| Output (after the draft) | Groundedness and citation support; number provenance; harm categories; Presidio on output |
| Process (graph) | Human approval before finalize, from a reviewer who did not submit the job |

Controls are switched in `config/guardrails.yaml`. The human approval gate is the exception: it is a hard stop in the graph and cannot be turned off.

## Consequences

- Each check emits an OpenTelemetry span and an evidence-log event, so a memo's bundle shows which controls ran and what they found.
- The red-team suite can measure attack success with each control on and off, because the switches are configuration.
- A control that cannot run, such as a cloud-only control in demo mode, records `not_evaluated` and never reports a pass (fail closed).
- Output failures go back to the drafter once as an automated fix; a second failure fails the job.
- The API gateway still enforces authentication, rate limits and request size, but it is not where content policy lives.
