# Prompts

Versioned prompt files, one per agent and version (for example `drafter/v1.md`). Each model call records the prompt's ID, version and SHA-256 for the evidence trail. Prompts never ask the model to do arithmetic.

**Built (week 3):** `intake/v1.md`, `intake/v2.md` (v2 asks for every fiscal year and for rows worded differently from their field; the first live runs extracted only the latest year), `analyst/v1.md`, `drafter/v1.md`, `drafter/v2.md` (v2 requires a cited claim in every section, including an empty "Risk factors"; the first live run left it empty), `drafter/v3.md` (v3 states which flags force "decline" and which block "approve", after policy CP-2026.2).

**Built (week 4):** `compliance/v1.md`, the compliance reviewer. It answers fixed topics chosen by code (prohibited basis, repayment capacity, and adverse-action reasons for a draft decline) against retrieved lending-reference passages, and must cite the passages it relied on. `compliance/v2.md` states that a prohibited basis is a characteristic of a person, not of the business; in the first live run, v1 flagged a business's founding year as the applicant's age. `drafter_revision/v1.md` revises a draft for a credit officer's comments under the same rules as a first draft; a comment cannot change the recommendation rules.

**Built (week 5):** `drafter_fix/v1.md`, the one automated fix after a guardrail finds a problem. It receives the problems with the claims they are about and must fix only those. No recordings exist for it: no recorded draft fails a check, so demo mode never calls it. **Planned:** registration in the MLflow prompt registry.
