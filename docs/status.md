# Status

Progress against the [development plan](dev-plan.md), milestone by milestone. The [README](../README.md) describes the target design; this page records what has been built and proven so far.

## Where it stands

Week 7, Gate 2 passed on a simulated fresh machine. The demo starts with one command and needs no API keys. A package goes from submission to a cited draft memo with credit policy and lending-reference findings, through layered guardrails (personal data masked, injected instructions quarantined, every number and citation checked), then waits for a credit officer to approve, reject or request a revision. Role overviews and "Explain this" show where every figure, ratio, flag and finding came from. An evaluation suite scores the platform against a 25-package golden set, 20 fairness pairs and 20 planted attacks, with gate thresholds set from the live baseline.

## Milestones

| Milestone | Target | What it proves | State |
|---|---|---|---|
| Repository scaffold | Nov 8 | Structure, tooling, and CI in place | ✅ Done |
| **Gate 1** · Architecture ready | Nov 15 | Supporting services run without keys; Regulus returns cited provisions | ✅ Passed: checks 7/7, design docs published |
| Week 3 · First vertical slice | Nov 22 | Submit a package, poll, get a cited draft memo traced in Phoenix | ✅ Done; run live on all ten packages |
| Week 4 · Compliance and human review | Nov 29 | A job pauses for review, survives a restart, and finalizes only on approval by someone other than the submitter | ✅ Passed: checks 14/14, including a restart while a job awaits review |
| Review page (week 7 item, brought forward) | Nov 11 | A credit officer can read a draft with its findings and decide in a browser | ✅ Built |
| Week 5 · Guardrails | Dec 6 | Each control can be switched off; redaction leaves every financial value unchanged; cloud-only controls report not evaluated in demo | ✅ Passed: checks 16/16; attack success 6/20 with guardrails on vs 20/20 off ([results](../evals/adversarial/v1/results.md)) |
| Week 6 · Evaluation baseline | Dec 13 | Eval report in MLflow; attack success measured with guardrails on and off; fairness results with sample size | ✅ Passed: checks 7/7. Live baseline 25 × 3: deterministic checks 100%, 66/75 acceptable recommendations, 17/20 fairness pairs consistent ([results](../evals/results/golden-live.md), [ADR 0008](adr/0008-evaluation-thresholds.md)); golden answers and grader labels still to review |
| Week 7 · Demo ready | Dec 20 | Role overviews, "Explain this", the keyless demo with all 25 packages recorded live, and an MCP tool gateway ([ADR 0014](adr/0014-tool-gateway.md)) | ✅ Done |
| **Gate 2** · Demo ready | Dec 20 | A stranger runs the demo in under 10 minutes with no API keys | ✅ Passed on a simulated fresh machine: checks 13/13. From `git clone` to a ready stack in 1.2 min (rerun Nov 14 with the tool gateway, cloned from the local commit), with no `.env`, the stack's images removed first and rebuilt without the build cache, on a fast connection. All 25 packages run end to end with no model call. A first run by someone new to the project is still to come ([checklist](first-run.md)) |
| Week 8 · CI release gates | Dec 27 | A deliberately degraded prompt fails the eval gate; a disabled guardrail fails the red-team gate | ✅ Done (Nov 15). Piece 1 (Nov 15): CI enforces the [ADR 0008](adr/0008-evaluation-thresholds.md) thresholds on every push (12 gates with no keys, 10 more on the live recordings through a freshly built stack, plus the Gate 1 and Gate 2 checks); switching a guardrail off fails the red-team gate. Piece 2 (Nov 15): Schemathesis contract tests in CI; they found seven places where the API and its published contract disagreed, all fixed. Piece 3 (Nov 15): a live five-package smoke gate when prompts, agents, the workflow or routing change, and the full golden set on demand; [acceptance test A](../evals/smoke/results.md) met: the current prompts pass 10/10, a degraded drafter prompt fails 3 gates. In CI once model keys are added as repository secrets. Piece 4 (Nov 15): an [alignment gate](../evals/alignment/README.md) of 24 scenarios (out-of-scope requests, decision-language bait, recommendation and omission pressure, distraction), built in this repo with genai_alignment's method; [first live run](../evals/alignment/v1/results.md) 24/24 held, 23 by the model and 1 by a guardrail fix. Both acceptance tests met. The live gates run in CI once model keys are repository secrets |
| **Gate 3** · Published | Jan 10 | CI gates enforced; docs and results public | ⬜ |

## What's next

Toward Gate 3 (CI gates enforced): the live gates need the model keys as GitHub repository secrets, and blocking a merge on a failed gate needs branch protection on `main`. Week 9, cloud deployment, is written but not yet deployed: Terraform for a lean demo that replays recorded model responses, about $28 a month ([ADR 0009](adr/0009-deployment-target.md)). Deploying it needs a personal Azure subscription. The [roadmap](../README.md#-roadmap) shows the phases after that.
