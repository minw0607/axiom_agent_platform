"""Record live model responses for the keyless demo, or check that recordings replay.

Record (spends tokens): runs each package through the real workflow in local mode, with
retrieval through the running gkn-mcp service, then two revision rounds with the demo
comments. Every live response is saved to a staging folder, never straight into
data/recorded/, so a failed run cannot leave the demo half replaced.

    uv run python scripts/record_demo.py --dry-run             # what would run, no calls
    uv run python scripts/record_demo.py                       # packages with no recording
    uv run python scripts/record_demo.py pkg-11 pkg-12

Check (no tokens): replays a recordings folder in demo mode, through GKN, the way the stack
will, and reports each package's result.

    uv run python scripts/record_demo.py --check build/recordings

Needs the stack running (`docker compose up -d`). Recording also needs AXIOM_MODE=local
model settings in .env (MODEL_ALIAS_MAP and a provider key); see data/recorded/README.md.
Settings come from the environment, then .env, then the demo's generated .demo/keys.env.
Keys are never printed.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver

ROOT = Path(__file__).resolve().parents[1]
# The comments the recorded revisions answer (also in scripts/seed_recordings.py).
DEMO_REVISION_COMMENTS = (
    "Add a sentence on liquidity that cites the current ratio.",
    "State the main condition you would propose, or say that none is needed.",
)
# Measured on the pkg-01..10 recordings: first draft plus two revision rounds.
TOKENS_PER_PACKAGE = 31_000


def load_env() -> None:
    """Fill unset variables from .env, then from the demo's generated keys."""
    for path in (ROOT / ".env", ROOT / ".demo" / "keys.env"):
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            name, sep, value = line.partition("=")
            if sep and not name.startswith("#") and not os.environ.get(name.strip()):
                os.environ[name.strip()] = value.strip()
    # The script runs on the host, where the services are on localhost.
    os.environ["GKN_MCP_URL"] = os.environ.get("GKN_MCP_URL_HOST", "http://localhost:8002/mcp")
    os.environ["REGULUS_MCP_URL"] = os.environ.get(
        "REGULUS_MCP_URL_HOST", "http://localhost:8001/mcp"
    )


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
    calls = state.get("model_calls", [])
    return {
        "error": state.get("error"),
        "recommendation": rec,
        "acceptable": rec in golden["acceptable_recommendations"],
        "figures": f"{right}/{checked}",
        "flags_match": want == have,
        "calls": len(calls),
        "rejected": sum(not c.valid for c in calls),
        "tokens": sum(c.input_tokens + c.output_tokens for c in calls),
        "origins": sorted({c.origin for c in calls}),
    }


async def run_package(ctx: Any, package_id: str, tag: str) -> dict[str, Any]:
    from axiom.catalog import load_ground_truth, load_package
    from axiom.domain import ReviewDecision
    from axiom.graph.workflow import resume_workflow, run_workflow
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
        await ctx.evidence.delete(job_id)


def row(package_id: str, r: dict[str, Any]) -> str:
    rec = f"{r['first_recommendation']} -> {r['recommendation']}"
    return (
        f"{package_id}  {r['error'] or 'ok':<26} {rec:<46} "
        f"{'yes' if r['acceptable'] else 'NO':<4} {r['figures']:<7} "
        f"{'yes' if r['flags_match'] else 'NO':<4} {r['calls']:>3} {r['rejected']:>3} "
        f"{r['tokens']:>7}  {','.join(r['origins'])}"
    )


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("packages", nargs="*", help="default: packages with no recording")
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

    from axiom.catalog import has_recordings, list_packages
    from axiom.jobs.worker import build_context
    from axiom.settings import Settings

    settings = Settings.from_env()
    catalog = [p["package_id"] for p in list_packages(settings.packages_dir)]
    if args.packages:
        unknown = sorted(set(args.packages) - set(catalog))
        if unknown:
            print(f"Unknown packages: {', '.join(unknown)}")
            return 2
        packages = args.packages
    elif args.check:
        packages = [p for p in catalog if has_recordings(args.check, p)]
    else:
        current = replace(settings, recordings_override=ROOT / "data" / "recorded")
        packages = [p for p in catalog if not has_recordings(current.recordings_dir, p)]
    if not packages:
        print("Nothing to do: every package has a recording.")
        return 0

    if not args.check:
        estimate = len(packages) * TOKENS_PER_PACKAGE
        print(f"Recording {len(packages)} packages live into {args.out}: {' '.join(packages)}")
        print(f"Estimated tokens: about {estimate:,} (first draft plus two revision rounds each)")
        if not os.environ.get("MODEL_ALIAS_MAP"):
            print("MODEL_ALIAS_MAP is not set: recording needs model settings in .env.")
            return 2
    else:
        print(f"Replaying {len(packages)} packages from {args.check} in demo mode through GKN")
    if not settings.mcp_shared_secret:
        print("MCP_SHARED_SECRET is not set (environment, .env or .demo/keys.env).")
        return 2
    if args.dry_run:
        return 0

    ctx = build_context(settings)
    tag = "check" if args.check else "record"
    print(
        "package status                     first -> after revisions"
        + " " * 20
        + "ok   figures flags calls rej tokens  origin"
    )
    failed = 0
    for package_id in packages:
        try:
            result = await run_package(ctx, package_id, tag)
        except Exception as exc:  # report it and carry on with the next package
            print(f"{package_id}  failed: {type(exc).__name__}: {exc}")
            failed += 1
            continue
        print(row(package_id, result), flush=True)
        failed += bool(result["error"] or not result["acceptable"])
    print(f"{len(packages) - failed}/{len(packages)} packages completed with an acceptable draft")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
