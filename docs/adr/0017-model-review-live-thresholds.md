# ADR 0017: Live thresholds for model documentation review

**Status:** Accepted (Nov 20, 2026). Step 3b of [ADR 0016](0016-app-contract.md).

## Context

Step 3a gave app 2 a golden set of six synthetic packages, each with an answer key, and release gates that run on scripted answers. Those answers come from the answer key, so every metric scores 1.0: they test the pipeline and the scoring, not a model. Step 3b runs real models (generic labels: the assessor and drafter on `frontier-a`) and sets the thresholds a live run must meet.

## What the live runs found

**Run 1** (6 packages × 3 repeats, about 239k tokens) scored well below the ceiling: 14 of 18 jobs completed, and the model kept only 58% of adequate sections as adequate. Each row now records the model's reason for every weak call and each guardrail's reason. Those reasons traced most misses to three faults on the platform's side, not the model's:

| Cause | What happened | Fix (commit 4f896ce) |
|---|---|---|
| The answer key | Sections the key called adequate left out a point the checklist asks for, such as why an approach was chosen or who approves changes. The model said so, the same way in every repeat. | Every adequate section now covers each point its checklist item expects. |
| The approval rule | "It does not state how changes are approved" matched the rule for "the model is approved". | The rule skips negations, questions about the documentation, and the model as an object. |
| The chatbot-aside rule | mdl-06 documents a tool that is itself a language model; the rule flagged any mention of "language model", so all three mdl-06 jobs failed. | The rule matches "as an AI" or "as a language model". |

**Run 2** (about 171k tokens), after those fixes:

| Metric | Run 1 | Run 2 | Threshold |
|---|---|---|---|
| Jobs completed | 0.78 | 1.00 | every run |
| Gaps, critical gaps, completeness, gaps named, citations valid, no decision stated (code) | 1.00 | 1.00 | every run |
| Status matches | 0.79 | 0.94 | 85% of runs |
| Weak sections found | 0.50 | 0.67 | mean 0.6 |
| Adequate sections kept | 0.58 | 0.95 | mean 0.9 |
| Findings covered (terms) | 0.85 | 0.69 | mean 0.6 |

Findings covered fell because mdl-06 now completes, exposing two drafting misses that run 1 hid. Run 2 left two real model misses and three small answer-key gaps:

- **Known miss, mdl-05 fairness:** customers over 70 get offers at half the average rate, and the documentation accepts it. The model rated that section adequate in all three repeats.
- **Known miss, mdl-03 fairness:** the report names the missing section each time but never says why it matters (the ranking decides how each customer is contacted and uses age band).
- **Answer key, fixed after run 2:** the shared data-quality section gave no completeness result; mdl-05's limitations did not say what the model was tested on; and mdl-06's fairness section ("no comparison across customer groups was made") is weak, as the model said, so the key now expects that finding. The dataset is now `model-review-golden-v2`, with four weak sections.

## Decision

Add the suite `model_review_golden_live` to `config/eval_gates.yaml`, applied over all 18 runs:

- **Code's part stays exact on every run**, as in the scripted suite: any miss is a bug.
- **Every job must complete.**
- **The model's judgment is gated a little below run 2:** status 85% of runs, weak sections found 0.6, adequate sections kept 0.9, findings covered 0.6. The known misses stay inside these marks, so the gate tracks them rather than excusing them.

`uv run python evals/gate.py --live --model-review` runs it (about 170k tokens). It is on demand only, as a `model_review` option of the Live gates workflow, not on every push, because of its cost.

## Consequences

- Six packages and four weak sections are a small set: these thresholds catch a broken prompt or a guardrail that stops jobs, not a small drift. One more miss in the wrong place can fail a mean.
- Run 2 was scored on the answer key before its last three fixes. The next live run, the step 3c recording run, is scored against `model-review-golden-v2` and confirms or adjusts these marks.
- Each live run spends tokens. The scripted suite stays in every CI run at no cost.
- The two known misses are prompt work for later: asking the assessor to compare outcomes across groups when a section reports them, and asking the drafter to say why a missing section matters.
