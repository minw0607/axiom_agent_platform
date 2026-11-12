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

## Implementation (week 5)

- **The interface** is in `src/axiom/guardrails/base.py`. A control has a name, a stage and a `check` that returns a verdict; the suite adds the job, node and switch state and records a `GuardrailResult`. Results hold codes, counts and a short reason, never document or draft text, because they go into the evidence log and the public memo.
- **Where the stages run.** Input controls run inside the intake node before documents are indexed or sent to a model. Generation and output controls run inside the drafter node on each draft, including a redraft after a credit officer's comments.
- **Switches.** `config/guardrails.yaml` must name every control the platform has, each `enabled: true` or `false`. An unknown or missing name stops the platform at startup, so a typo cannot switch a control off silently.
- **Fail closed.** A control that is switched off, raises an error, or needs a cloud service that is not connected records `not_evaluated` with the reason. None of these paths can produce `passed`.
- **Evidence.** Each result becomes a `guardrail_checked` event in the job's hash chain: input results before the node's model calls, the others after them. The evidence bundle lists every result; the public memo shows each control's latest result, for the current draft.
- **Names.** The cloud injection screen is called `injection_shield` in the platform, because the public API never contains the word "prompt"; it uses Azure AI Content Safety's Prompt Shields.
- **Input order.** `pii_redaction` runs first, then `injection_screen` and `injection_shield`, so the screens see redacted text. Each input control can rewrite the documents for the controls after it; the rest of the job, GKN's index included, sees only the redacted documents.
- **Redaction never touches the numbers.** Table rows and headings are not edited, and a finding that overlaps an amount, percentage or fiscal year is dropped. As a final check, the list of financial values before and after must match, or the redaction is refused and recorded `not_evaluated`. Only identifiers of people are masked; dates, places and organizations are business facts. The business name is never masked. Line breaks are kept, so source line numbers still hold.
- **Redaction and routing.** A model call is marked `pii_unredacted` unless redaction ran on the documents. If redaction is switched off or fails, the router sends those calls only to a permitted local model, and stops the job if there is none (`no_permitted_model`).
- **Quarantine stops the job.** Any match from the injection screen stops the job in intake, before indexing or any model call, with the public code `document_quarantined`. Dropping the document and drafting without it was rejected: a memo written from part of the package, with no sign of what was removed, would mislead the credit officer.
- **Known limits.** The small spaCy model takes single capitalized words for names (it tagged "Loan" in "# Loan application"), so a name must be two or more capitalized words, and a lone surname is missed. The injection screen is a pattern list: it catches common phrasings and disguises such as zero-width and full-width characters, and rewording evades it. The adversarial set measures both.
- **Draft checks.** Three rails (`decision_language`, `investment_advice`, `on_task`) and two evidence checks (`number_provenance`, `citation_support`) run on every draft, in code rather than a rails framework, so each finding is a pattern code or a number and is tested directly. A number traces if it matches an extracted figure, a ratio or reconciliation total, a policy threshold, or a number in the documents or retrieved passages, within the rounding its shown precision allows. Numbers inside identifiers (FY2025, CP-4.1) and small counts are skipped.
- **The automated fix.** If any draft check is `triggered`, the drafter gets one fix call (`drafter_fix/v1`) with the problems and the claims they concern, and the fixed draft is checked again. A second failure fails the job with `guardrail_failed`. Each drafter run gets at most one fix, counted in `automated_fixes`, apart from the credit officer's revisions. `not_evaluated` never triggers a fix. The evidence keeps both rounds' results, with positions and codes only.
- **Recommendation labels.** The plan worded the recommendation as proceed, proceed with conditions, and do not proceed. The platform keeps `approve`, `approve_with_conditions`, `decline` and `refer`, which every recording and the policy rules already use. They are draft labels; the `decision_language` rail keeps any of them from being stated as a decision made.
