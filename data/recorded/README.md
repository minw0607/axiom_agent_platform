# Recorded model responses

Demo mode (`AXIOM_MODE=demo`) replays these files instead of calling a model, so the stack runs with no API keys. Each file holds one response, keyed by prompt, prompt version, package and document, for example `intake/v2/pkg-01/income_statement.json`.

These are **live recordings**: real model responses saved during local-mode runs, each marked `origin: recorded`. Each file also stores the SHA-256 of the prompt it answered. When a replayed recording was made from a different prompt, the evidence trail says so.

The tests do not use these files. They replay scripted responses in `tests/fixtures/recorded/` (`origin: scripted`, built from ground truth by `scripts/seed_recordings.py`), so test results never depend on a model's judgment.

## What the current recordings show

**pkg-01 to pkg-10** were recorded Nov 11, 2026 with prompts intake v2, analyst v1, drafter v3 and compliance v2, under policy CP-2026.2, with passages retrieved through the GKN MCP server as in the `docker compose` stack. Every recommendation matched the expected result and every extracted figure matched the ground truth, value and source line. One drafter call (pkg-07) was rejected once and corrected on retry; both attempts are recorded, so the demo replays the retry too. The results are summarized in `docs/ai-assisted-engineering.md` (first live run). The compliance reviews were recorded afterwards, with the other nodes replaying these files, so each review is of the draft the demo shows.

**pkg-11 to pkg-25** were recorded Nov 14, 2026 with `scripts/record_demo.py`, using the same prompts, policy and GKN retrieval. Each package ran end to end: a first draft and two revision rounds in one run, 179 calls in all, every one valid on its first attempt. Every extracted figure matched the ground truth, value and source line, and every package raised exactly its expected risk flags. 13 of the 15 draft recommendations are acceptable under the golden answers. Two are not, and the demo keeps them as recorded:

- **pkg-21** (missing cash flow statement): the golden answer is "refer"; the draft says "approve with conditions".
- **pkg-23** (loan below the policy minimum): the golden answer is "refer"; the draft says "approve".

In both, the credit policy rules catch what the drafter missed. pkg-21 triggers two high-severity findings, required documents (CP-3) and three years of statements (CP-3.1). pkg-23 triggers a high-severity loan amount in scope finding (CP-1.1). So the credit officer's queue puts both first. This is the open "refer" gap from the week 6 baseline (`evals/results/golden-live.md`).

**Revisions.** `drafter_revision/v1/<package>.r1` and `.r2`, with the matching `compliance/v2/<package>.r1` and `.r2`, are two revision rounds recorded live for every package with these comments: "Add a sentence on liquidity that cites the current ratio." and "State the main condition you would propose, or say that none is needed." All were valid on first attempts. In demo mode a revision replays the recording for its round, **whatever comments the reviewer typed**, so a demo revision shows the mechanism, not a response to the comment. Local mode sends the actual comments to the model.

## Re-recording

1. In `.env`, set `OPENAI_API_KEY`, `MODEL_ALIAS_MAP` (for example `small-a=openai:<deployment>,frontier-a=openai:<deployment>`) and, for a gateway, `OPENAI_BASE_URL` and the optional settings in `.env.example`.
2. With the stack running, record into a staging folder: `uv run python scripts/record_demo.py --dry-run` lists the packages and the token estimate (about 31,000 per package), and `uv run python scripts/record_demo.py pkg-11 pkg-12` records them. The script runs local mode with `AXIOM_RECORD=1` and GKN retrieval, then two revision rounds with the comments above. Retrieval must go through GKN: in-memory retrieval names passages differently, so a drafter recording made with it cites passages the stack never returns, and the job fails validation.
3. Check the staging folder replays, with no tokens: `uv run python scripts/record_demo.py --check build/recordings`. Copy it into this folder only when every package completes.
4. Review the new files before committing them. Recordings must never contain real deployment names, keys or personal data.
