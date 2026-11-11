# Recorded model responses

Demo mode (`AXIOM_MODE=demo`) replays these files instead of calling a model, so the stack runs with no API keys. Each file holds one response, keyed by prompt, prompt version, package and document, for example `intake/v1/pkg-01/income_statement.json`.

Every file states its `origin`:

- **`scripted`**: written by `scripts/seed_recordings.py`. Intake answers come from the ground truth, and the analyst and drafter text is a template filled from the workflow's own inputs. These show the pipeline working; they are not model output. They pass the same validation a model's output must pass.
- **`recorded`**: a real model response saved during a local-mode run.

Each file also stores the SHA-256 of the prompt it answered. When a replayed recording was made from a different prompt, the evidence trail says so.

## Replacing scripted responses with real ones

1. In `.env`, set `AXIOM_MODE=local`, `OPENAI_API_KEY`, and `MODEL_ALIAS_MAP` (for example `small-a=openai:<deployment>,frontier-a=openai:<deployment>`).
2. Set `AXIOM_RECORD=1` and run each catalog package through the workflow.
3. Review the new files before committing them. Recordings must never contain real deployment names, keys or personal data.
