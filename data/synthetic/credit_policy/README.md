# Credit policy

The fictional credit policy: leverage, coverage and concentration limits, and documentation requirements. In docker compose, gkn-mcp loads this folder as its read-only `credit_policy` collection (it skips this README). The rule engine reads the same thresholds from structured config.

**Built (week 3):** `credit_policy.md`, policy version CP-2026.2 (CP-2026.2 adds CP-6.3, a pro forma DSCR floor of 1.00x below which a request is declined).
