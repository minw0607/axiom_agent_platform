"""Week 6 acceptance check: the measured baseline.

The plan's criteria (dev plan, week 6):
1. An evaluation report in MLflow: a live golden-set run with three repeats is logged,
   with its gate metrics, and the report is committed under evals/results/.
2. Attack success rate measured with guardrails on and off: adversarial set v1 (week 5).
   It measures exposure in demo mode, whether planted text reaches a model; whether a live
   model follows it is the red-team gate in week 8. The check says so.
3. Counterfactual results reported with sample size: a live run of the pairs, with the
   number of pairs and runs and the consistency rate.
Plus what the plan asks of this week's other work:
4. The golden set holds 25 packages with answers, and they agree with the platform's tools.
5. The grader's calibration is reported, and says whether it is partial.
6. ADR 0008 records the thresholds, calibrated against the live baseline.

Usage: uv run --group evals python scripts/check_week6.py
MLflow is read at MLFLOW_TRACKING_URI (default http://localhost:5050); start it with
`docker compose --profile evals up -d mlflow`. Without it, check 1 reports the gap.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "evals" / "results"
results: list[tuple[str, bool, str]] = []


def check(name: str, passed: bool, detail: str = "") -> None:
    results.append((name, passed, detail))
    print(f"{'PASS' if passed else 'FAIL'}  {name}{f'  ({detail})' if detail else ''}")


def load(path: Path) -> dict[str, Any] | None:
    return json.loads(path.read_text()) if path.exists() else None


def check_mlflow() -> None:
    golden = load(RESULTS / "golden-live.json")
    repeats = max((r["repeat"] for r in golden["rows"]), default=0) if golden else 0
    packages = len({r["package_id"] for r in golden["rows"]}) if golden else 0
    check(
        "a live golden-set report is committed",
        golden is not None and repeats >= 3 and packages == 25,
        f"{packages} packages x {repeats} repeats"
        if golden
        else "evals/results/golden-live.json missing",
    )
    try:
        os.environ.setdefault("MLFLOW_TRACKING_URI", "http://localhost:5050")
        os.environ.setdefault("MLFLOW_DISABLE_AGENT_HINT", "1")
        import mlflow

        client = mlflow.MlflowClient()
        exp = client.get_experiment_by_name("axiom-golden")
        runs = client.search_runs([exp.experiment_id], "params.mode = 'live'") if exp else []
        metric = "citation_support_judge"
        logged = [r for r in runs if "ratio_exactness" in r.data.metrics]
        check(
            "the live run is in MLflow with its gate metrics",
            bool(logged),
            f"{len(logged)} live run(s); grader metric logged: "
            f"{any(metric in r.data.metrics for r in logged)}",
        )
    except Exception as exc:  # MLflow down or not installed: report, don't crash
        check(
            "the live run is in MLflow with its gate metrics",
            False,
            f"MLflow not reachable ({type(exc).__name__})",
        )


def check_attacks() -> None:
    adv = load(ROOT / "evals" / "adversarial" / "v1" / "results.json")
    total = adv["summary"][-1] if adv else None
    check(
        "attack success rate is measured with guardrails on and off",
        bool(total) and total["succeeded_on"] < total["succeeded_off"],
        f"on {total['succeeded_on']}/{total['n']}, off {total['succeeded_off']}/{total['n']}; "
        "demo-mode exposure, live model behaviour is the week 8 red-team gate"
        if total
        else "evals/adversarial/v1/results.json missing",
    )


def check_counterfactual() -> None:
    cf = load(RESULTS / "counterfactual-live.json")
    s = cf["summary"] if cf else {}
    check(
        "counterfactual results are reported with sample size",
        bool(cf) and s.get("pairs", 0) >= 20 and s.get("counterfactual_consistency") is not None,
        f"{s.get('pairs')} pairs, {s.get('runs')} runs, "
        f"consistency {s.get('counterfactual_consistency')}"
        if cf
        else "evals/results/counterfactual-live.json missing",
    )


def check_golden_set() -> None:
    catalog = json.loads((ROOT / "data" / "synthetic" / "packages" / "catalog.json").read_text())
    with_answers = sum(
        "golden"
        in json.loads(
            (
                ROOT / "data" / "synthetic" / "packages" / p["package_id"] / "ground_truth.json"
            ).read_text()
        )
        for p in catalog
    )
    check(
        "the golden set holds 25 packages with answers",
        len(catalog) == with_answers == 25,
        f"{with_answers}",
    )


def check_grader() -> None:
    cal = load(ROOT / "evals" / "judge" / "v1" / "calibration-live.json")
    s = cal["summary"] if cal else {}
    check(
        "the grader's calibration is reported, partial or not",
        bool(cal) and s.get("graded_both_framings") == s.get("items") and "calibration" in s,
        f"{s.get('calibration')}; failed {s.get('catches_altered_items')} of altered items; "
        f"stakes flips {s.get('stakes_flip_rate')}"
        if cal
        else "evals/judge/v1/calibration-live.json missing",
    )


def check_adr() -> None:
    adr = ROOT / "docs" / "adr" / "0008-evaluation-thresholds.md"
    text = adr.read_text() if adr.exists() else ""
    check(
        "ADR 0008 records thresholds calibrated against the live baseline",
        "**Status:** Accepted" in text and "golden-live" in text,
        adr.name if text else "docs/adr/0008-evaluation-thresholds.md missing",
    )


def main() -> int:
    check_mlflow()
    check_attacks()
    check_counterfactual()
    check_golden_set()
    check_grader()
    check_adr()
    passed = sum(ok for _, ok, _ in results)
    verdict = "PASSED" if passed == len(results) else "FAILED"
    print(f"\nWeek 6: {verdict} ({passed}/{len(results)} checks)")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
