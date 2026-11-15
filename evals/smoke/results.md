# Live smoke gate: acceptance test A (Nov 15, 2026)

Week 8's acceptance test A: a deliberately degraded prompt fails the eval gate. Both runs used the same five packages, the same models (small-a and frontier-a through the router) and in-memory retrieval, about 66k tokens each; the only difference was the drafter prompt.

## With the current prompts: passes

`uv run python evals/gate.py --live` (4 min)

| Suite | Gate | Rule | Result | Detail |
|---|---|---|---|---|
| smoke (live) | job_completion | every True | pass | 5/5 runs |
| smoke (live) | extraction_accuracy_critical | every 1.0 | pass | 5/5 runs |
| smoke (live) | extraction_accuracy_all | mean 0.99 | pass | 1.000 over 5 runs |
| smoke (live) | ratio_exactness | every 1.0 | pass | 5/5 runs |
| smoke (live) | flag_recall | every 1.0 | pass | 5/5 runs |
| smoke (live) | flag_precision | every 1.0 | pass | 5/5 runs |
| smoke (live) | number_provenance | every 1.0 | pass | 5/5 runs |
| smoke (live) | citation_coverage | every 1.0 | pass | 5/5 runs |
| smoke (live) | key_finding_recall_terms | mean 0.9 | pass | 1.000 over 4 runs; main 1.000, at most 0.05 below; excused: pkg-21, pkg-23 |
| smoke (live) | recommendation_acceptable | every True | pass | 4/4 runs; excused: pkg-21, pkg-23 |

10/10 gates passed.

## With the degraded drafter prompt: fails

`uv run python evals/gate.py --live --degrade` (4 min), with [`degraded_drafter.md`](degraded_drafter.md) in place of `prompts/drafter/v3.md`

| Suite | Gate | Rule | Result | Detail |
|---|---|---|---|---|
| smoke (live, degraded drafter) | job_completion | every True | **FAIL** | 2/5 runs; failing: pkg-04 (False), pkg-06 (False), pkg-21 (False) |
| smoke (live, degraded drafter) | extraction_accuracy_critical | every 1.0 | pass | 2/2 runs |
| smoke (live, degraded drafter) | extraction_accuracy_all | mean 0.99 | pass | 1.000 over 2 runs |
| smoke (live, degraded drafter) | ratio_exactness | every 1.0 | pass | 2/2 runs |
| smoke (live, degraded drafter) | flag_recall | every 1.0 | pass | 2/2 runs |
| smoke (live, degraded drafter) | flag_precision | every 1.0 | pass | 2/2 runs |
| smoke (live, degraded drafter) | number_provenance | every 1.0 | pass | 2/2 runs |
| smoke (live, degraded drafter) | citation_coverage | every 1.0 | pass | 2/2 runs |
| smoke (live, degraded drafter) | key_finding_recall_terms | mean 0.9 | **FAIL** | 0.500 over 2 runs; main 1.000, at most 0.05 below; excused: pkg-21, pkg-23 |
| smoke (live, degraded drafter) | recommendation_acceptable | every True | **FAIL** | 1/2 runs; failing: pkg-11 (False); excused: pkg-21, pkg-23 |

7/10 gates passed.

Three of five jobs failed, and the two memos that were drafted missed half their key findings; one recommended the wrong outcome. The deterministic checks that pass did so on the two drafted memos only. The run predates the gate reporting each failed job's reason, so the failure codes were not recorded; the gate now shows them.

A replay of the recordings passes either prompt unchanged, because replays return the recorded answers whatever the prompt says. That is why a change to a prompt, an agent, the workflow or routing runs this gate (`.github/workflows/live-gates.yml`).
