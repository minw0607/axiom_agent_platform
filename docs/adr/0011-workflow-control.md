# ADR 0011: Workflow control

**Status:** Accepted (week 3)

## Context

Something must decide which agent runs next and when the workflow stops. Credit work follows a known sequence: read the package, analyze it, draft the memo, check it against policy, have a person decide. Auditors and model risk reviewers need to know that every memo took the same path, and why a run stopped.

## Options considered

| Option | Strengths | Weaknesses |
|---|---|---|
| Rule-based pipeline | Repeatable; every transition explainable from code; easy to test | Cannot adapt the path to unusual packages |
| LLM supervisor | Can skip or repeat steps on its own judgment | Path varies between runs; adds a model call per step; a prompt-injected document could steer the workflow (threat model T-01, T-05) |
| Hybrid (rules, with the model choosing among allowed branches) | Some flexibility inside fixed limits | More complex; the flexibility is not needed yet |

## Decision

A **rule-based supervisor in plain code, with no model**, decides every transition in the LangGraph workflow (`src/axiom/graph/workflow.py`):

- Nodes run in a fixed order: intake, analyst, drafter. Compliance, human review and finalize are added in week 4.
- After each node, the supervisor stops the run if the node recorded an error, and otherwise moves to the next node.
- Loop limits are counters in state, not model judgment: at most two human revisions and one automated fix (dev plan section 7.4).

## Consequences

- Every successful run of the same package visits the same nodes in the same order, and the evidence trail records which nodes completed.
- A document cannot change the path, because no model output feeds a routing decision.
- Unusual packages get no special path. They are handled by flags, reconciliation checks and the human reviewer.
- If a later phase adds conditional branches, they will be code conditions on typed state, with this ADR revised.
