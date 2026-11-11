# Prompts

Versioned prompt files, one per agent and version (for example `drafter/v1.md`). Each model call records the prompt's ID, version and SHA-256 for the evidence trail. Prompts never ask the model to do arithmetic.

**Built (week 3):** `intake/v1.md`, `analyst/v1.md`, `drafter/v1.md`, `drafter/v2.md` (v2 requires a cited claim in every section, including an empty "Risk factors"; the first live run left it empty). **Planned:** registration in the MLflow prompt registry.
