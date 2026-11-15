"""Release gates (ADR 0008): run the evaluation suites and fail if any threshold is missed.

The thresholds live in config/eval_gates.yaml. Each suite's runner turns its results into
rows (one per run) with an `id`, whether the job `completed`, and the metrics the config
names; `evaluate` applies the rules and knows nothing about lending, so another app can
bring its own suites and thresholds (ADR 0015).

    uv run python evals/gate.py           # no keys, no stack: golden set and fairness pairs
                                          # on scripted recordings, attacks with the
                                          # guardrails as configured
    uv run python evals/gate.py --stack   # the golden set on the live recordings, through
                                          # the running stack's tool gateway and GKN

Exits 1 if a gate fails. In GitHub Actions it also writes the table to the job summary.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from axiom.settings import REPO_ROOT

HERE = Path(__file__).parent
GATES_FILE = REPO_ROOT / "config" / "eval_gates.yaml"
RULES = ("every", "mean", "rate")
SHOWN = 6  # failing runs listed per gate


# Evaluating rows against rules (app-independent) -------------------------------------------


@dataclass
class GateResult:
    suite: str
    metric: str
    rule: str
    ok: bool
    detail: str


def load_gates(path: Path = GATES_FILE) -> dict[str, dict[str, dict[str, Any]]]:
    raw = yaml.safe_load(path.read_text()) or {}
    suites = raw.get("suites")
    if not isinstance(suites, dict):
        raise ValueError(f"{path.name} needs a 'suites' mapping")
    for suite, metrics in suites.items():
        for metric, rule in (metrics or {}).items():
            kinds = [k for k in RULES if k in rule]
            unknown = set(rule) - {*RULES, "exceptions"}
            if len(kinds) != 1 or unknown:
                raise ValueError(f"{suite}.{metric}: give exactly one of {', '.join(RULES)}")
    return suites


def _passes(value: Any, target: Any) -> bool:
    if isinstance(target, bool):
        return value is target
    return value is not None and float(value) >= float(target) - 1e-9


def evaluate(suite: str, rows: list[dict[str, Any]], rules: dict[str, Any]) -> list[GateResult]:
    """Apply each rule to the rows. A metric is judged on completed runs only, except
    `job_completion`; a value that was not evaluated (None) never passes an `every` rule."""
    results = []
    for metric, rule in rules.items():
        kind = next(k for k in RULES if k in rule)
        target = rule[kind]
        excused = rule.get("exceptions") or {}
        pool = [
            r
            for r in rows
            if r["id"] not in excused and (metric == "job_completion" or r["completed"])
        ]
        if not any(metric in r for r in rows):
            raise ValueError(f"{suite}: no run reports '{metric}' (check config/eval_gates.yaml)")
        if kind == "every":
            bad = [r for r in pool if not _passes(r.get(metric), target)]
            ok = not bad
            shown = ", ".join(f"{r['id']} ({r.get(metric)})" for r in bad[:SHOWN])
            detail = f"{len(pool) - len(bad)}/{len(pool)} runs" + (
                f"; failing: {shown}" if bad else ""
            )
        else:
            values = [r.get(metric) for r in pool if r.get(metric) is not None]
            if kind == "rate":
                actual = sum(v is True for v in values) / len(values) if values else 0.0
            else:
                actual = statistics.mean(float(v) for v in values) if values else 0.0
            ok = bool(values) and actual >= float(target) - 1e-9
            detail = f"{actual:.3f} over {len(values)} runs"
        if excused:
            detail += f"; excused: {', '.join(excused)}"
        results.append(GateResult(suite, metric, f"{kind} {target}", ok, detail))
    return results


def report(results: list[GateResult]) -> str:
    lines = ["| Suite | Gate | Rule | Result | Detail |", "|---|---|---|---|---|"]
    lines += [
        f"| {r.suite} | {r.metric} | {r.rule} | {'pass' if r.ok else '**FAIL**'} | {r.detail} |"
        for r in results
    ]
    failed = sum(not r.ok for r in results)
    lines.append("")
    lines.append(f"{len(results) - failed}/{len(results)} gates passed.")
    return "\n".join(lines) + "\n"


# The credit memo app's suites (lending-specific runners) -----------------------------------


def _golden_row(scored: dict[str, Any]) -> dict[str, Any]:
    done = scored["outcome"] == "drafted"
    return {**scored, "id": scored["package_id"], "completed": done, "job_completion": done}


async def golden_scripted() -> list[dict[str, Any]]:
    import baseline

    report_ = await baseline.run_golden("demo", 1, None, judge=False)
    return [_golden_row(r) for r in report_["rows"]]


async def counterfactual_scripted() -> list[dict[str, Any]]:
    import baseline

    report_ = await baseline.run_counterfactual("demo", 1)
    return [{**r, "completed": True} for r in report_["rows"]]


async def attacks_configured(guardrails_file: Path | None = None) -> list[dict[str, Any]]:
    sys.path.insert(0, str(HERE / "adversarial"))
    import harness

    committed = {
        c["id"]: c for c in json.loads((harness.SET / "results.json").read_text())["cases"]
    }
    switches = harness.configured_switches(guardrails_file)
    off = sorted(name for name, on in switches.items() if not on)
    if off:
        print(f"Guardrails switched off in config/guardrails.yaml: {', '.join(off)}")
    rows = []
    for case in harness.load_cases():
        outcome = await harness.run_case(case, switches)
        was_stopped = not committed[case["id"]]["on"]["succeeded"]
        rows.append(
            {
                "id": case["id"],
                "completed": True,
                "stopped_as_committed": not (was_stopped and outcome.succeeded),
            }
        )
    return rows


def _load_stack_env() -> None:
    """The settings a replay through the running stack needs: the demo's generated keys
    and the gateway on localhost. Values from the environment or .env win; none is printed."""
    for path in (REPO_ROOT / ".env", REPO_ROOT / ".demo" / "keys.env"):
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            name, sep, value = line.partition("=")
            if sep and not name.startswith("#") and not os.environ.get(name.strip()):
                os.environ[name.strip()] = value.strip()
    os.environ["AXIOM_MODE"] = "demo"
    os.environ["AXIOM_RECORDINGS_DIR"] = str(REPO_ROOT / "data" / "recorded")
    os.environ["TOOL_GATEWAY_URL"] = os.environ.get(
        "TOOL_GATEWAY_URL_HOST", "http://localhost:8003/mcp"
    )


async def golden_stack() -> list[dict[str, Any]]:
    """The 25 live recordings replayed through the stack's gateway and GKN, as the keyless
    demo runs them, and scored against the golden answers."""
    _load_stack_env()
    import baseline

    from axiom.catalog import list_packages, load_ground_truth, load_package
    from axiom.jobs.worker import build_context
    from axiom.mcp_clients.client import acting_as
    from axiom.settings import Settings

    settings = Settings.from_env()
    if "operator" not in settings.tool_gateway_tokens:
        raise SystemExit("tool gateway tokens not found: start the stack (docker compose up)")
    ctx = build_context(settings)
    rows = []
    for item in list_packages(settings.packages_dir):
        pid = item["package_id"]
        job = f"eval-gate-{pid}"  # the id baseline.run_one gives the job
        try:
            state = await baseline.run_one(ctx, load_package(settings.packages_dir, pid), "gate")
            rows.append(
                _golden_row(baseline.score(state, load_ground_truth(settings.packages_dir, pid)))
            )
        finally:
            with acting_as("operator"):
                await ctx.evidence.delete(job)
    return rows


Runner = Callable[[], Coroutine[Any, Any, list[dict[str, Any]]]]
LOCAL: dict[str, tuple[str, Runner]] = {
    "golden": ("golden", golden_scripted),
    "counterfactual": ("counterfactual", counterfactual_scripted),
    "attacks": ("attacks", attacks_configured),
}
STACK: dict[str, tuple[str, Runner]] = {
    "golden (live recordings)": ("golden", golden_stack),
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--stack", action="store_true", help="replay through the running stack")
    parser.add_argument("--gates", type=Path, default=GATES_FILE)
    args = parser.parse_args(argv)
    gates = load_gates(args.gates)
    sys.path.insert(0, str(HERE))
    results: list[GateResult] = []
    for label, (suite, runner) in (STACK if args.stack else LOCAL).items():
        rows = asyncio.run(runner())
        results += evaluate(label, rows, gates[suite])
    text = report(results)
    print(text)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        title = "Release gates, through the stack" if args.stack else "Release gates"
        with open(summary, "a", encoding="utf-8") as f:
            f.write(f"## {title}\n\n{text}\n")
    return 1 if any(not r.ok for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
