# Evaluations

Suites run by CI and the nightly job: golden-set quality, red-team, alignment scenarios, counterfactual fairness pairs, and judge calibration. Evaluation tools install in a separate dependency group so they never conflict with the runtime.

**Built (week 5):** [`adversarial/`](adversarial/): adversarial set v1, 20 synthetic attacks (injected instructions, personal data, and model output that followed an injection), and a harness that runs them in demo mode with every guardrail on and with every one off. `uv run python evals/adversarial/harness.py` rewrites [`v1/results.md`](adversarial/v1/results.md); a test fails if the committed results go stale or a case stops meeting its expectation.

**Built (week 6), draft:** [`golden/v1/answers.md`](golden/v1/answers.md), the golden set's expected answers for 25 packages, written out for review. The answers themselves live in each package's `ground_truth.json`.

**Built (week 6): the scoring runner.** [`baseline.py`](baseline.py) runs the golden set and the counterfactual pairs through the full workflow and scores each memo against the plan's release-gate metrics: extraction accuracy, ratio exactness, flag recall and precision, an acceptable recommendation, number provenance, citation coverage and key-finding coverage. Key findings are matched by their terms for now; an AI grader (piece 3) will judge them properly.

```bash
uv run python evals/baseline.py                  # demo mode, no keys: writes results/*-demo.*
docker compose --profile evals up -d mlflow      # optional: the MLflow UI at http://localhost:5050
MLFLOW_TRACKING_URI=http://localhost:5050 uv run --group evals python evals/baseline.py --mlflow
```

Demo mode replays scripted recordings built from the ground truth, so its results test the pipeline and the scoring, not a model; [results/golden-demo.md](results/golden-demo.md) is the ceiling a live run is compared with. It already shows the two drafting gaps the golden set found (pkg-21, pkg-23). Live mode (`--mode live`, with `AXIOM_MODE=local` and model keys) is week 6 piece 4.

**Built (week 6): counterfactual pairs v1.** [`counterfactual/v1/pairs.yaml`](counterfactual/v1/pairs.yaml): 20 pairs that add one sentence about the owner, differing only in a prohibited basis under Regulation B (race, national origin, religion, sex, marital status, age, public assistance). The pass condition is identical ratios, flags, rule findings and recommendation. In demo mode all 20 are consistent by construction; the test that matters is the live run. Redaction masks full names, so the two name pairs never reach a model, and the report says so. These are tests on synthetic data, not a fair-lending conclusion.

**Built (week 6): the grader.** [`judge/`](judge/): an LLM judge for what code cannot score, run on the router's `judge` route. In local mode that is `small-a`, a different model from the drafter, with no fallback; a memo the grader's own model drafted (the drafter's fallback) gets `not_evaluated` grades, because a model must not grade its own work. It answers two questions, each with versioned prompts in `judge/prompts/`: does a claim's cited source support it (`citation_support_judge`, gate 0.90), and does the memo state a key finding (`key_finding_recall_judge`, gate 0.80). The runner adds both in live mode.

Before the grader's numbers count, it is calibrated: [`judge/v1/calibration.json`](judge/v1/calibration.json) holds 30 items built from the live recorded drafts (20 citation items, 7 of them altered on purpose; 10 finding items, 4 paired with another package's finding). A person labels them blind on a private labelling page, and [`judge/calibrate.py`](judge/calibrate.py) reports agreement and Cohen's kappa with those labels, and how often a high-stakes note changes a verdict (dev plan 9.4: it must not).

**Status: partial calibration.** The first labelling round did not produce usable labels (see the engineering log, Nov 12), so agreement with a person is not yet measured. Until it is, the grader is checked only against the 11 items whose right answer is "no" by construction and for stakes bias, and its scores on memos are reported as uncalibrated, not used as a gate.

**First live run (Nov 12): [`judge/v1/calibration-live.md`](judge/v1/calibration-live.md).** All 30 items graded in both framings (60 calls, about 43,000 tokens). The grader failed all 11 items built to need a "no", and the high-stakes note changed none of its 30 verdicts. It also marks claims about the platform's own calculations (a risk flag, a ratio such as DSCR 2.52, a reconciliation result) "not supported", because the cited document lines cannot show them. That is the rule applied correctly, but it would fail real memos for the kind of claim rather than its truth; piece 4 gives the grader the tool outputs as sources. The answers are recorded under `data/recorded/judge_*`, so the run replays without a model.

MLflow installs in its own dependency group (`uv sync --group evals`) and runs as an optional compose service, so it never enters the API or worker image. Port 5050, because macOS AirPlay Receiver holds port 5000.

**Planned:** Weeks 6-8 of the [development plan](/docs/dev-plan.md): the golden set, the red-team gate against live models, alignment and counterfactual suites.
