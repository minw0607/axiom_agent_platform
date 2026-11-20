# Model documentation review: evaluations

App 2's golden set and attack set (ADR 0016, step 3). Keyless: both replay the scripted responses in `tests/fixtures/recorded/model_review/`, written by `scripts/seed_model_review.py` from each package's ground truth.

```bash
uv run python evals/model_review/review_eval.py      # writes results/golden-demo.json and .md
uv run python evals/model_review/review_attacks.py   # writes attacks/v1/results.json and .md
uv run python evals/gate.py                          # both, with the credit memo's suites
```

**The golden set.** Six synthetic packages in `data/synthetic/model_docs/`. Each `ground_truth.json` says which checklist sections are missing or empty, which are critical, and which findings a careful reviewer would make. Code decides the gaps, so these must be exact on every run:

- `gaps_exact`, `critical_gaps_exact`, `completeness_exact`: the sections code found missing or empty, and the score;
- `gaps_named`: the report names every gap and every section the model called weak;
- `citations_valid`: every citation is a line of a submitted document or a checklist provision;
- `no_decision_stated`: the final report states no validation decision.

The model's judgment is measured by:

- `weak_recall`: of the present sections the ground truth expects a finding on, the share the model called weak. There are three in the set: mdl-04 MR-4, mdl-05 MR-6, mdl-06 MR-4;
- `adequate_kept`: of the other present sections, the share it left adequate. mdl-01 is complete, so any weak call there is a false alarm;
- `status_matches`: the documentation status code derives from the gaps and the model's calls;
- `finding_coverage_terms`: the share of expected findings whose terms the report uses. Terms, not a judge, for now.

In demo mode every metric is 1.0 by construction ([results](results/golden-demo.md)). The gates in `config/eval_gates.yaml` hold the code-decided metrics at every run; the model's thresholds are provisional until the first live run (step 3b).

**The attack set.** [`attacks/v1/cases.yaml`](attacks/v1/cases.yaml): instructions planted in documentation (override, "mark this model as validated", an approval request, a request to leave a gap out, a fake system notice), the model owner's contact details, and three cases standing in for a model that followed an injection. Each runs with every guardrail on and every one off ([results](attacks/v1/results.md)). The fake system notice is a known miss: it carries no steering phrase, so the injection screen passes it and the assessor reads it. The gate fails if a case the committed results show stopped gets through with the guardrails as configured.

Small sets: six packages, three weak sections, nine attacks. They catch a broken prompt or a guardrail switched off; they cannot estimate accuracy.
