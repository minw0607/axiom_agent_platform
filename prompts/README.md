# Prompts

Versioned prompt files, one per agent and version (for example `drafter/v1.md`). Each model call records the prompt's ID, version and SHA-256 for the evidence trail. Prompts never ask the model to do arithmetic.

**Built (week 3):** `intake/v1.md`, `intake/v2.md` (v2 asks for every fiscal year and for rows worded differently from their field; the first live runs extracted only the latest year), `analyst/v1.md`, `drafter/v1.md`, `drafter/v2.md` (v2 requires a cited claim in every section, including an empty "Risk factors"; the first live run left it empty), `drafter/v3.md` (v3 states which flags force "decline" and which block "approve", after policy CP-2026.2). **Planned:** registration in the MLflow prompt registry.
