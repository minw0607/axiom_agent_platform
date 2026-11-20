"""Record live model responses for the keyless demo, or check that recordings replay.

Record (spends tokens): runs each package through the real workflow in local mode, with
tool calls through the running tool gateway, then two revision rounds with the demo comments.
Every live response is saved to a staging folder, never straight into data/recorded/, so a
failed run cannot leave the demo half replaced.

    uv run python scripts/record_demo.py --dry-run             # what would run, no calls
    uv run python scripts/record_demo.py                       # packages with no recording
    uv run python scripts/record_demo.py pkg-11 pkg-12
    uv run python scripts/record_demo.py --app model_review    # app 2's packages

Check (no tokens): replays a recordings folder in demo mode, through the gateway, the way the stack
will, and reports each package's result.

    uv run python scripts/record_demo.py --check build/recordings
    uv run python scripts/record_demo.py --app model_review --check build/recordings

For model documentation review (app 2), the first draft of each package is also scored against
the golden answer key, the way evals/model_review/review_eval.py scores a live run, and the rows
are written to build/model_review-recording-scores.json.

Needs the stack running (`docker compose up -d`). Recording also needs AXIOM_MODE=local
model settings in .env (MODEL_ALIAS_MAP and a provider key); see data/recorded/README.md.
Settings come from the environment, then .env, then the demo's generated .demo/keys.env.
Keys are never printed.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver

ROOT = Path(__file__).resolve().parents[1]
APPS = ("credit_memo", "model_review")
# The comments the recorded revisions answer (also in scripts/seed_recordings.py).
DEMO_REVISION_COMMENTS = (
    "Add a sentence on liquidity that cites the current ratio.",
    "State the main condition you would propose, or say that none is needed.",
)
# App 2's comments (also in scripts/seed_model_review.py).
REVIEW_REVISION_COMMENTS = (
    "Say which gaps must be closed before validation can start.",
    "Name the provision behind each critical gap.",
)
# First draft plus two revision rounds. Credit memo: measured on the pkg-01..10 recordings.
# Model review: from the step 3b live evaluation runs, with room for an automated fix.
TOKENS_PER_PACKAGE = {"credit_memo": 31_000, "model_review": 22_000}
SCORES = ROOT / "build" / "model_review-recording-scores.json"


def load_env() -> None:
    """Fill unset variables from .env, then from the demo's generated keys."""
    for path in (ROOT / ".env", ROOT / ".demo" / "keys.env"):
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            name, sep, value = line.partition("=")
            if sep and not name.startswith("#") and not os.environ.get(name.strip()):
                os.environ[name.strip()] = value.strip()
    # The script runs on the host, where the gateway is on localhost. Each workflow step
    # uses its own token; cleaning up a job uses the operator token.
    os.environ["TOOL_GATEWAY_URL"] = os.environ.get(
        "TOOL_GATEWAY_URL_HOST", "http://localhost:8003/mcp"
    )


def call_totals(state: dict[str, Any]) -> dict[str, Any]:
    calls = state.get("model_calls", [])
    return {
        "calls": len(calls),
        "rejected": sum(not c.valid for c in calls),
        "tokens": sum(c.input_tokens + c.output_tokens for c in calls),
        "origins": sorted({c.origin for c in calls}),
    }


def summarize(state: dict[str, Any], truth: dict[str, Any]) -> dict[str, Any]:
    golden = truth["golden"]
    draft = state.get("draft")
    rec = draft.draft_recommendation if draft else None
    fs = state.get("financials")
    checked = right = 0
    for year, fields in truth["years"].items():
        for name, raw in fields.items():
            fig = fs.years.get(int(year), {}).get(name) if fs else None
            checked += 1
            right += fig is not None and (str(fig.value), fig.source.line) == (
                str(raw["value"]),
                raw["line"],
            )
    want = {f["code"] for f in golden["expected_flags"]}
    have = {f.code for f in state.get("risk_flags", [])}
    return {
        "error": state.get("error"),
        "recommendation": rec,
        "acceptable": rec in golden["acceptable_recommendations"],
        "figures": f"{right}/{checked}",
        "flags_match": want == have,
        **call_totals(state),
    }


async def run_package(ctx: Any, package_id: str, tag: str) -> dict[str, Any]:
    from axiom.apps.credit_memo.catalog import load_ground_truth, load_package
    from axiom.apps.credit_memo.domain import ReviewDecision
    from axiom.apps.credit_memo.graph import resume_workflow, run_workflow
    from axiom.mcp_clients.client import acting_as
    from axiom.memory.checkpoint import serializer
    from axiom.settings import Settings

    settings = Settings.from_env()
    package = load_package(settings.packages_dir, package_id)
    job_id = f"{tag}-{package_id}"
    saver = InMemorySaver(serde=serializer())
    try:
        state: Any = await run_workflow(ctx, job_id, package, saver)
        first = summarize(state, load_ground_truth(settings.packages_dir, package_id))
        for n, comment in enumerate(DEMO_REVISION_COMMENTS, 1):
            if state.get("error"):
                break
            review = ReviewDecision(decision="revise", comments=comment, reviewer="demo", round=n)
            state = await resume_workflow(ctx, job_id, review, saver)
        final = summarize(state, load_ground_truth(settings.packages_dir, package_id))
        final["first_recommendation"] = first["recommendation"]
        return final
    finally:
        with acting_as("operator"):
            await ctx.evidence.delete(job_id)


async def run_review_package(ctx: Any, package_id: str, tag: str) -> dict[str, Any]:
    """App 2: a first draft, scored against the answer key, then two revision rounds."""
    from axiom.apps.model_review.catalog import load_ground_truth, load_package, packages_dir
    from axiom.apps.model_review.domain import ReviewDecision
    from axiom.apps.model_review.graph import resume_workflow, run_workflow
    from axiom.memory.checkpoint import serializer
    from axiom.settings import Settings

    sys.path.insert(0, str(ROOT / "evals" / "model_review"))
    import review_eval

    folder = packages_dir(Settings.from_env().data_dir)
    package = load_package(folder, package_id)
    job_id = f"{tag}-{package_id}"
    saver = InMemorySaver(serde=serializer())
    state: Any = await run_workflow(ctx, job_id, package, saver)
    first = review_eval.score(state, load_ground_truth(folder, package_id), package)
    revisions = 0
    for n, comment in enumerate(REVIEW_REVISION_COMMENTS, 1):
        if state.get("error"):
            break
        review = ReviewDecision(decision="revise", comments=comment, reviewer="demo", round=n)
        state = await resume_workflow(ctx, job_id, review, saver)
        revisions += not state.get("error")
    return {
        "error": state.get("error"),
        "first_draft": first,
        "revisions": revisions,
        "provisions_status": state.get("provisions_status"),
        **call_totals(state),
    }


def row(package_id: str, r: dict[str, Any]) -> str:
    rec = f"{r['first_recommendation']} -> {r['recommendation']}"
    return (
        f"{package_id}  {r['error'] or 'ok':<26} {rec:<46} "
        f"{'yes' if r['acceptable'] else 'NO':<4} {r['figures']:<7} "
        f"{'yes' if r['flags_match'] else 'NO':<4} {r['calls']:>3} {r['rejected']:>3} "
        f"{r['tokens']:>7}  {','.join(r['origins'])}"
    )


def review_row(package_id: str, r: dict[str, Any]) -> str:
    s = r["first_draft"]
    if not s["job_completion"]:
        return f"{package_id}  first draft failed: {s.get('error')} {s.get('guardrails_triggered')}"
    cover = s["finding_coverage_terms"]
    return (
        f"{package_id}  {r['error'] or 'ok':<12} {s['documentation_status']:<20} "
        f"{'yes' if s['status_matches'] else 'NO':<4} "
        f"{','.join(s['weak_expected']) or '-':<6} {','.join(s['weak_found']) or '-':<12} "
        f"{'-' if cover is None else f'{cover:.2f}':<5} {r['revisions']:>3} "
        f"{r['calls']:>3} {r['rejected']:>3} {r['tokens']:>7}  {','.join(r['origins'])}"
    )


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("packages", nargs="*", help="default: packages with no recording")
    parser.add_argument("--app", choices=APPS, default="credit_memo")
    parser.add_argument("--out", type=Path, default=ROOT / "build" / "recordings")
    parser.add_argument("--check", type=Path, help="replay this folder instead of recording")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    load_env()
    if args.check:
        os.environ["AXIOM_MODE"] = "demo"
        os.environ["AXIOM_RECORDINGS_DIR"] = str(args.check.resolve())
        os.environ.pop("AXIOM_RECORD", None)
    else:
        os.environ["AXIOM_MODE"] = "local"
        os.environ["AXIOM_RECORD"] = "1"
        os.environ["AXIOM_RECORDINGS_DIR"] = str(args.out.resolve())

    from axiom.core.registry import get_app
    from axiom.settings import Settings

    settings = Settings.from_env()
    app = get_app(args.app)
    catalog = [p["package_id"] for p in app.inputs(settings)]
    if args.packages:
        unknown = sorted(set(args.packages) - set(catalog))
        if unknown:
            print(f"Unknown packages: {', '.join(unknown)}")
            return 2
        packages = args.packages
    elif args.check:
        packages = [p for p in catalog if app.has_recordings(settings, p)]
    else:
        current = replace(settings, recordings_override=ROOT / "data" / "recorded")
        packages = [p for p in catalog if not app.has_recordings(current, p)]
    if not packages:
        print("Nothing to do: every package has a recording.")
        return 0

    if not args.check:
        estimate = len(packages) * TOKENS_PER_PACKAGE[args.app]
        print(f"Recording {len(packages)} {args.app} packages live into {args.out}")
        print(f"Packages: {' '.join(packages)}")
        print(f"Estimated tokens: about {estimate:,} (first draft plus two revision rounds each)")
        if not os.environ.get("MODEL_ALIAS_MAP"):
            print("MODEL_ALIAS_MAP is not set: recording needs model settings in .env.")
            return 2
    else:
        print(f"Replaying {len(packages)} {args.app} packages from {args.check} in demo mode")
    tokens = settings.tool_gateway_tokens
    if "operator" not in tokens or not any(c.startswith(f"{args.app}.") for c in tokens):
        print("Tool gateway tokens are not set (environment, .env or .demo/keys.env).")
        return 2
    if args.dry_run:
        return 0

    tag = "check" if args.check else "record"
    if args.app == "credit_memo":
        from axiom.jobs.worker import build_context

        ctx = build_context(settings)
        print(
            "package status                     first -> after revisions"
            + " " * 20
            + "ok   figures flags calls rej tokens  origin"
        )
    else:
        ctx = app.build_context(settings)
        print(
            "package status       first draft status   match weak   found        cover"
            " rev calls rej tokens  origin"
        )
    failed = 0
    scored = []
    for package_id in packages:
        try:
            if args.app == "credit_memo":
                result = await run_package(ctx, package_id, tag)
            else:
                result = await run_review_package(ctx, package_id, tag)
        except Exception as exc:  # report it and carry on with the next package
            print(f"{package_id}  failed: {type(exc).__name__}: {exc}")
            failed += 1
            continue
        if args.app == "credit_memo":
            print(row(package_id, result), flush=True)
            failed += bool(result["error"] or not result["acceptable"])
        else:
            print(review_row(package_id, result), flush=True)
            failed += bool(result["error"] or result["revisions"] < 2)
            scored.append({"repeat": 1, **result["first_draft"]})
    if scored and not args.check:
        SCORES.parent.mkdir(parents=True, exist_ok=True)
        SCORES.write_text(json.dumps({"rows": scored}, indent=1, default=str), encoding="utf-8")
        print(f"First drafts scored against the answer key: {SCORES}")
    done = "with two revisions" if args.app == "model_review" else "with an acceptable draft"
    print(f"{len(packages) - failed}/{len(packages)} packages completed {done}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
