# Recorded model responses

Demo mode (`AXIOM_MODE=demo`) replays these files instead of calling a model, so the stack runs with no API keys. Each file holds one response, keyed by prompt, prompt version, package and document, for example `intake/v2/pkg-01/income_statement.json`.

These are **live recordings**: real model responses saved during local-mode runs, each marked `origin: recorded`. Each file also stores the SHA-256 of the prompt it answered. When a replayed recording was made from a different prompt, the evidence trail says so.

The tests do not use these files. They replay scripted responses in `tests/fixtures/recorded/` (`origin: scripted`, built from ground truth by `scripts/seed_recordings.py`), so test results never depend on a model's judgment.

## What the current recordings show

Recorded Nov 11, 2026 with prompts intake v2, analyst v1 and drafter v3, under policy CP-2026.2. Every package completed on first attempts, and every extracted figure matched the ground truth, value and source line. The results are summarized in `docs/ai-assisted-engineering.md` (first live run).

## Re-recording

1. In `.env`, set `OPENAI_API_KEY`, `MODEL_ALIAS_MAP` (for example `small-a=openai:<deployment>,frontier-a=openai:<deployment>`) and, for a gateway, `OPENAI_BASE_URL` and the optional settings in `.env.example`.
2. Run each catalog package through the workflow with `AXIOM_MODE=local` and `AXIOM_RECORD=1`. Record into a separate folder first (`AXIOM_RECORDINGS_DIR`), and check every package completes before replacing these files.
3. Review the new files before committing them. Recordings must never contain real deployment names, keys or personal data.
