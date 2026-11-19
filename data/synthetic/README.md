# Synthetic data

Generated borrower packages (three years of statements, application, business description), the fictional credit policy, the golden set, counterfactual pairs, and adversarial documents. Numbers are generated first and text second, so every figure has a known ground truth. No real customer, employer, or client data ever goes here.

**Built (week 3):** ten packages in `packages/`, made by `scripts/generate_synthetic.py`. Each has five documents, a `package.json`, and a `ground_truth.json` holding every figure's value and source line plus the expected ratios. `catalog.json` lists them:

| Package | Scenario | Built to raise |
|---|---|---|
| pkg-01, pkg-02 | healthy | nothing |
| pkg-03 | moderate | nothing; DSCR close to the minimum |
| pkg-04 | weak_dscr | pro forma DSCR below 1.25x |
| pkg-05 | leverage_breach | pro forma leverage above 3.50x |
| pkg-06 | inconsistent_figures | FY2024 balance sheet does not balance |
| pkg-07 | missing_year | two years of statements instead of three |
| pkg-08 | concentration | one customer at 48% of revenue |
| pkg-09 | revenue_decline | revenue down 14% |
| pkg-10 | low_liquidity | current ratio below 1.20x |

**Built (week 6): the golden set.** Fifteen more packages probe the edges of the policy, and every package's `ground_truth.json` now holds a `golden` section: the expected risk flags, the acceptable draft recommendations, and the key findings a good memo states. The generator derives these from the credit policy text on its own, and a test checks that they agree with the platform's flag tool on every package. They are a **draft until reviewed**: [evals/golden/v1/answers.md](../../evals/golden/v1/answers.md) shows them in one page for review.

| Package | Scenario | Built to test |
|---|---|---|
| pkg-11 | dscr_at_minimum | pro forma DSCR exactly 1.25x: meets the minimum, no flag |
| pkg-12 | dscr_just_below_minimum | pro forma DSCR 1.24x |
| pkg-13 | leverage_at_maximum | pro forma leverage exactly 3.50x: no flag |
| pkg-14 | multiple_breaches | DSCR, liquidity and leverage all fail |
| pkg-15 | negative_ebitda | EBITDA below zero: decline |
| pkg-16 | growth_with_oversized_loan | strong growth, but the loan sinks DSCR below the 1.00x floor |
| pkg-17 | concentration_disclosure | one customer at 32%: disclosure, a warning |
| pkg-18 | concentration_at_maximum | one customer at exactly 40%: not above the maximum |
| pkg-19 | revenue_decline_at_trigger | revenue down exactly 10%: no flag |
| pkg-20 | two_warnings | revenue decline and concentration disclosure together |
| pkg-21 | missing_cash_flow | no cash flow statement, so DSCR cannot be computed |
| pkg-22 | small_imbalance | FY2025 balance sheet off by 3 dollars |
| pkg-23 | loan_below_minimum | a 200,000 request, below the policy's 250,000 scope |
| pkg-24 | low_interest_coverage | interest coverage 1.19x, and leverage just above 3.50x |
| pkg-25 | healthy_large_loan | a 5,000,000 request, at the top of the policy's scope |

Boundary loan amounts were tuned so the rounded ratio lands exactly on the limit. The new packages have scripted test recordings but no live ones yet, so the keyless demo still offers pkg-01 to pkg-10.

The fictional credit policy is in `credit_policy/`; its numeric thresholds are in `config/apps/credit_memo/policy_thresholds.yaml`. Adversarial documents are in `evals/adversarial/` (week 5).

Counterfactual pairs are in `evals/counterfactual/` (week 6): they add a sentence to a package's business description at run time, so they need no files here.

## Model documentation packages (app 2)

`model_docs/` holds six documentation packages for fictional models, each with a development report, a data description and a monitoring plan, written by `scripts/generate_model_docs.py` for app 2, model documentation review ([ADR 0016](../../docs/adr/0016-app-contract.md)). Each package's `ground_truth.json` records, for every item of the section checklist (`config/apps/model_review/checklist.yaml`), whether the generator wrote the section and on which lines, and what a reviewer should find.

| Package | Model | Built to test |
|---|---|---|
| mdl-01 | Deposit run-off forecast | Complete documentation (12 of 12 sections) |
| mdl-02 | Card fraud alerting | No monitoring metrics (a critical gap) and no change management |
| mdl-03 | Collections prioritization | No fairness assessment, although the ranking affects customers |
| mdl-04 | Mortgage prepayment | Performance tested only on the data the model was fitted to; sensitivity testing a placeholder |
| mdl-05 | Savings offer propensity | No data lineage (a critical gap); privacy a placeholder; a fairness result accepted without analysis |
| mdl-06 | Call summary assistant (generative) | No limitations and no human oversight; performance judged by reading 40 summaries |

Code decides whether a section is present; a weak section such as mdl-04's performance testing is present to code and needs a model's judgment, which step 2b adds. All models, teams and figures are fictional.
