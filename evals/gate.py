"""Release gates (ADR 0008): run the evaluation suites and fail if any threshold is missed.

The thresholds live in config/eval_gates.yaml. Each suite's runner turns its results into
rows (one per run) with an `id`, whether the job `completed`, and the metrics the config
names; `evaluate` applies the rules and knows nothing about lending, so another app can
bring its own suites and thresholds (ADR 0015).

    uv run python evals/gate.py           # no keys, no stack: golden set and fairness pairs
                                          # on scripted recordings, attacks with the
                                          # guardrails as configured, and the same for
                                          # model documentation review (app 2)
    uv run python evals/gate.py --stack   # the golden set on the live recordings, through
                                          # the running stack's tool gateway and GKN
    uv run python evals/gate.py --live    # five packages against real models (spends
                                          # tokens, about 66k): run when prompts, agents,
                                          # the workflow or routing change
    uv run python evals/gate.py --live --full     # 25 packages x 3 repeats (about 1M tokens)
    uv run python evals/gate.py --live --degrade  # the same with a deliberately degraded
                                                  # drafter prompt: it must fail (acceptance A)
    uv run python evals/gate.py --live --alignment  # 24 alignment scenarios (about 315k
                                                    # tokens; evals/alignment)
    uv run python evals/gate.py --live --model-review  # app 2's six packages x 3 against
                                                       # real models (about 170k tokens)

Exits 1 if a gate fails. In GitHub Actions it also writes the table to the job summary.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import statistics
import sys
import tempfile
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from axiom.settings import REPO_ROOT

HERE = Path(__file__).parent
GATES_FILE = REPO_ROOT / "config" / "eval_gates.yaml"
RULES = ("every", "mean", "rate")
OPTIONS = {"exceptions", "max_drop"}
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
    suites = _load(path).get("suites")
    if not isinstance(suites, dict):
        raise ValueError(f"{path.name} needs a 'suites' mapping")
    for suite, metrics in suites.items():
        for metric, rule in (metrics or {}).items():
            kinds = [k for k in RULES if k in rule]
            unknown = set(rule) - {*RULES, *OPTIONS}
            if len(kinds) != 1 or unknown:
                raise ValueError(f"{suite}.{metric}: give exactly one of {', '.join(RULES)}")
            if "max_drop" in rule and kinds != ["mean"]:
                raise ValueError(f"{suite}.{metric}: max_drop applies to a mean rule")
    return suites


def _load(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text()) or {}


def load_live(path: Path = GATES_FILE) -> dict[str, dict[str, Any]]:
    """The live runs: which packages, how many repeats, and the accepted main-branch results
    that max_drop rules compare with."""
    return _load(path).get("live") or {}


def _passes(value: Any, target: Any) -> bool:
    if isinstance(target, bool):
        return value is target
    return value is not None and float(value) >= float(target) - 1e-9


def _mean(rows: list[dict[str, Any]], metric: str) -> float | None:
    values = [float(r[metric]) for r in rows if r.get(metric) is not None]
    return statistics.mean(values) if values else None


def evaluate(
    suite: str,
    rows: list[dict[str, Any]],
    rules: dict[str, Any],
    baseline: list[dict[str, Any]] | None = None,
) -> list[GateResult]:
    """Apply each rule to the rows. A metric is judged on completed runs only, except
    `job_completion`; a value that was not evaluated (None) never passes an `every` rule.
    A `max_drop` rule also compares the mean with the baseline's (the accepted main-branch
    run) over the same packages."""
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
            # A failed job shows its reason (a code and guardrail names, never memo text).
            why = "failed_why" if metric == "job_completion" else metric
            shown = ", ".join(f"{r['id']} ({r.get(why, r.get(metric))})" for r in bad[:SHOWN])
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
            if kind == "rate":
                bad = [r for r in pool if r.get(metric) is not True]
                shown = ", ".join(
                    f"{r['id']} ({r.get('failed_why', r.get(metric))})" for r in bad[:SHOWN]
                )
                detail += f"; not met: {shown}" if bad else ""
            if "max_drop" in rule:
                ids = {r["id"] for r in pool}
                main = _mean(
                    [b for b in baseline or [] if b["id"] in ids and b["completed"]], metric
                )
                if main is None:
                    raise ValueError(f"{suite}.{metric}: max_drop needs a baseline for these runs")
                floor = main - float(rule["max_drop"])
                ok = ok and actual >= floor - 1e-9
                detail += f"; main {main:.3f}, at most {rule['max_drop']} below"
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
    row = {**scored, "id": scored["package_id"], "completed": done, "job_completion": done}
    if not done:
        rails = scored.get("guardrails_triggered") or []
        row["failed_why"] = scored["outcome"] + (f": {', '.join(rails)}" if rails else "")
    return row


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

    return await _attacks(harness, guardrails_file)


async def _attacks(harness: Any, guardrails_file: Path | None) -> list[dict[str, Any]]:
    """An app's attack set (its harness module: SET, load_cases, configured_switches,
    run_case) run with the guardrails as configured, against its committed results."""
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


# Model documentation review's suites (app 2) ----------------------------------------------


async def model_review_golden(mode: str = "demo", repeats: int = 1) -> list[dict[str, Any]]:
    sys.path.insert(0, str(HERE / "model_review"))
    import review_eval

    report_ = await review_eval.run_golden(mode, repeats, None)
    if mode == "live":
        print(review_eval.write(report_))
    rows = []
    for r in report_["rows"]:
        done = r["outcome"] == "drafted"
        row = {**r, "id": r["package_id"], "completed": done}
        if not done:
            rails = r.get("guardrails_triggered") or []
            row["failed_why"] = r["error"] + (f": {', '.join(rails)}" if rails else "")
        rows.append(row)
    return rows


async def model_review_attacks(guardrails_file: Path | None = None) -> list[dict[str, Any]]:
    sys.path.insert(0, str(HERE / "model_review"))
    import review_attacks

    return await _attacks(review_attacks, guardrails_file)


def _load_env_files(*paths: Path) -> None:
    """Fill unset variables from these files; the environment wins. Nothing is printed."""
    for path in paths:
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            name, sep, value = line.partition("=")
            if sep and not name.startswith("#") and not os.environ.get(name.strip()):
                os.environ[name.strip()] = value.strip()


def _load_stack_env() -> None:
    """The settings a replay through the running stack needs: the demo's generated keys
    and the gateway on localhost."""
    _load_env_files(REPO_ROOT / ".env", REPO_ROOT / ".demo" / "keys.env")
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

    from axiom.apps.credit_memo.catalog import list_packages, load_ground_truth, load_package
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


DEGRADED_DRAFTER = HERE / "smoke" / "degraded_drafter.md"


def _live_env(degrade: bool) -> None:
    """Real models, with the routing and keys from the environment or .env. Retrieval is
    in memory, as in the week 6 baseline the results are compared with, and nothing is
    recorded over the demo's recordings."""
    _load_env_files(REPO_ROOT / ".env")
    if not os.environ.get("MODEL_ALIAS_MAP"):
        raise SystemExit("--live needs model settings (MODEL_ALIAS_MAP and a provider key)")
    os.environ["AXIOM_MODE"] = "local"
    os.environ["AXIOM_RECORD"] = ""
    for name in ("GKN_MCP_URL", "REGULUS_MCP_URL", "TOOL_GATEWAY_URL", "MCP_SHARED_SECRET"):
        os.environ[name] = ""
    if degrade:
        from axiom.apps.credit_memo.agents.base import PROMPT_VERSIONS

        prompts = Path(tempfile.mkdtemp(prefix="axiom-degraded-")) / "prompts"
        shutil.copytree(REPO_ROOT / "prompts", prompts)
        target = prompts / "credit_memo" / "drafter" / f"{PROMPT_VERSIONS['drafter']}.md"
        target.write_text(DEGRADED_DRAFTER.read_text(encoding="utf-8"), encoding="utf-8")
        os.environ["AXIOM_PROMPTS_DIR"] = str(prompts)
        print(f"Degraded drafter prompt in place of {target.relative_to(prompts.parent)}")


async def golden_live(packages: list[str] | None, repeats: int) -> list[dict[str, Any]]:
    import baseline

    report_ = await baseline.run_golden("live", repeats, packages, judge=False)
    return [_golden_row(r) for r in report_["rows"]]


def per_package(rows: list[dict[str, Any]], rules: dict[str, Any]) -> list[dict[str, Any]]:
    """One row per package over its repeats (ADR 0008): every repeat must complete; a
    recommendation is acceptable in at least two of three repeats; an `every` metric takes
    the package's worst completed run and a `mean` metric its average."""
    by_id: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        by_id.setdefault(r["id"], []).append(r)
    out = []
    for pid, runs in by_id.items():
        done = [r for r in runs if r["completed"]]
        acceptable = sum(r.get("recommendation_acceptable") is True for r in runs)
        row: dict[str, Any] = {
            "id": pid,
            "completed": bool(done),
            "job_completion": len(done) == len(runs),
            "recommendation_acceptable": 3 * acceptable >= 2 * len(runs),
        }
        failed = [r["failed_why"] for r in runs if "failed_why" in r]
        if failed:
            row["failed_why"] = "; ".join(sorted(set(failed)))
        for metric, rule in rules.items():
            if metric in row:
                continue
            values = [r[metric] for r in done if r.get(metric) is not None]
            if any(metric in r for r in runs):
                row[metric] = (
                    None
                    if not values
                    else statistics.mean(float(v) for v in values)
                    if "mean" in rule
                    else min(values)
                )
        out.append(row)
    return out


def run_alignment(args: argparse.Namespace, gates: dict[str, Any]) -> list[GateResult]:
    """The alignment scenarios against real models (evals/alignment)."""
    spec = load_live(args.gates)["alignment"]
    _live_env(degrade=False)
    sys.path.insert(0, str(HERE / "alignment"))
    import align as alignment

    scenarios = alignment.load_scenarios(REPO_ROOT / spec["scenarios"])
    rows = asyncio.run(alignment.run("live", scenarios))
    print(alignment.table(rows))
    _summary(f"## Alignment scenarios\n\n{alignment.table(rows)}\n")
    return evaluate("alignment (live)", rows, gates[spec["suite"]])


def run_model_review(args: argparse.Namespace, gates: dict[str, Any]) -> list[GateResult]:
    """App 2's golden set against real models. Rules apply over every run, not per package:
    the model's judgment is gated on its average (ADR 0017)."""
    spec = load_live(args.gates)["model_review"]
    _live_env(degrade=False)
    rows = asyncio.run(model_review_golden("live", spec["repeats"]))
    return evaluate("model review (live)", rows, gates[spec["suite"]])


def _summary(text: str) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(text)


def run_live(args: argparse.Namespace, gates: dict[str, Any]) -> list[GateResult]:
    if args.alignment:
        return run_alignment(args, gates)
    if args.model_review:
        return run_model_review(args, gates)
    spec = load_live(args.gates)["full" if args.full else "smoke"]
    rules = gates[spec["suite"]]
    packages = spec["packages"] if spec["packages"] != "all" else None
    _live_env(args.degrade)
    rows = per_package(asyncio.run(golden_live(packages, spec["repeats"])), rules)
    ids = {r["id"] for r in rows}
    accepted = json.loads((REPO_ROOT / spec["baseline"]).read_text())["rows"]
    baseline = per_package([_golden_row(r) for r in accepted if r["package_id"] in ids], rules)
    label = (
        f"{'full' if args.full else 'smoke'} (live{', degraded drafter' if args.degrade else ''})"
    )
    return evaluate(label, rows, rules, baseline)


Runner = Callable[[], Coroutine[Any, Any, list[dict[str, Any]]]]
LOCAL: dict[str, tuple[str, Runner]] = {
    "golden": ("golden", golden_scripted),
    "counterfactual": ("counterfactual", counterfactual_scripted),
    "attacks": ("attacks", attacks_configured),
    "model review golden": ("model_review_golden", model_review_golden),
    "model review attacks": ("model_review_attacks", model_review_attacks),
}
STACK: dict[str, tuple[str, Runner]] = {
    "golden (live recordings)": ("golden", golden_stack),
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--stack", action="store_true", help="replay through the running stack")
    parser.add_argument("--live", action="store_true", help="real models: spends tokens")
    parser.add_argument("--full", action="store_true", help="with --live: all packages, 3 repeats")
    parser.add_argument(
        "--degrade", action="store_true", help="with --live: a degraded drafter prompt"
    )
    parser.add_argument(
        "--alignment", action="store_true", help="with --live: the alignment scenarios"
    )
    parser.add_argument(
        "--model-review", action="store_true", help="with --live: app 2's golden set"
    )
    parser.add_argument("--gates", type=Path, default=GATES_FILE)
    args = parser.parse_args(argv)
    gates = load_gates(args.gates)
    sys.path.insert(0, str(HERE))
    if (args.full or args.degrade or args.alignment or args.model_review) and not args.live:
        parser.error("--full, --degrade, --alignment and --model-review go with --live")
    if (args.alignment or args.model_review) and (
        args.full or args.degrade or (args.alignment and args.model_review)
    ):
        parser.error("--alignment and --model-review each run on their own")
    results: list[GateResult] = []
    if args.live:
        results = run_live(args, gates)
    for label, (suite, runner) in ({} if args.live else STACK if args.stack else LOCAL).items():
        rows = asyncio.run(runner())
        results += evaluate(label, rows, gates[suite])
    text = report(results)
    print(text)
    title = (
        "Release gates, live models"
        if args.live
        else "Release gates, through the stack"
        if args.stack
        else "Release gates"
    )
    _summary(f"## {title}\n\n{text}\n")
    return 1 if any(not r.ok for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
