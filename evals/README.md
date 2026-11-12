# Evaluations

Suites run by CI and the nightly job: golden-set quality, red-team, alignment scenarios, counterfactual fairness pairs, and judge calibration. Evaluation tools install in a separate dependency group so they never conflict with the runtime.

**Built (week 5):** [`adversarial/`](adversarial/): adversarial set v1, 20 synthetic attacks (injected instructions, personal data, and model output that followed an injection), and a harness that runs them in demo mode with every guardrail on and with every one off. `uv run python evals/adversarial/harness.py` rewrites [`v1/results.md`](adversarial/v1/results.md); a test fails if the committed results go stale or a case stops meeting its expectation.

**Planned:** Weeks 6-8 of the [development plan](/docs/dev-plan.md): the golden set, the red-team gate against live models, alignment and counterfactual suites.
