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

The fictional credit policy is in `credit_policy/`; its numeric thresholds are in `config/policy_thresholds.yaml`.

**Planned:** golden set, counterfactual pairs and adversarial documents, weeks 4-6.
