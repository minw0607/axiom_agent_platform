# CLAUDE.md

Conventions for Claude Code sessions in this repo. The full design is in [docs/dev-plan.md](docs/dev-plan.md); read the relevant section before starting a feature.

## Purpose

A governed multi-agent platform for commercial lending: a versioned API, a rule-based LangGraph workflow of specialist agents, layered guardrails, human approval, and an evidence log for every memo. All data is synthetic.

## Architecture principles

- The API is the contract. Responses never name a model, agent, or prompt; those appear only in the auditor-only evidence endpoint.
- LLMs reason; code calculates. Every number in a memo is an extracted figure with a source location or a deterministic tool output.
- Every claim is cited and supported. Claims are structured objects with source references.
- Humans decide. No final memo without approval from a reviewer who didn't submit the job.
- Evidence by default. Job events go to an append-only, hash-chained log.
- Fail closed. A control that can't run records `not_evaluated`, never a pass.
- Configuration over code for routing, thresholds, and guardrail switches.

## Commands

```bash
uv sync                    # install, including dev tools
uv run pytest              # tests
uv run ruff check .        # lint
uv run ruff format .       # format
uv run mypy                # type check (strict)
docker compose up          # full stack, from week 2
```

## Conventions

- Python 3.11, managed with uv. Source lives in `src/axiom/`.
- On macOS, `import axiom` can fail outside pytest because the editable-install `.pth` file gets flagged hidden. Fix with `chflags nohidden .venv/lib/python3.11/site-packages/*.pth`; pytest is unaffected because it adds `src` to the path itself.
- Typed Pydantic models at every boundary (API, agent outputs, MCP calls).
- No arithmetic in prompts.
- Every new endpoint gets a contract test; every graph node gets a unit test with a mocked model.
- Write tests first for deterministic code and API contracts.
- Small PRs: roughly 4 to 6 per week of the plan.
- Record significant decisions as ADRs in `docs/adr/`.
- Log what was delegated to Claude Code, what was changed, and what was learned in `docs/ai-assisted-engineering.md`.

## Do not

- Add real customer, employer, or client data, or any employer, client, or personal references.
- Put secrets in code or committed config; use `.env` locally and Key Vault in the cloud.
- Put model names in API responses, or real model deployment names in published output (use logical aliases).
- Treat SR 26-2 as a requirement this platform satisfies; it is applied by analogy only.
