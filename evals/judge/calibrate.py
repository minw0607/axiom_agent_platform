"""Calibrate the grader against a person's labels, and test it for stakes bias.

    uv run python evals/judge/calibrate.py --mode live      # grades 30 items x 2 framings
    uv run python evals/judge/calibrate.py --mode live --mlflow

Reads evals/judge/v1/calibration.json (the items) and evals/judge/v1/labels.json (the
person's labels, exported from the labelling page). Each item is graded twice, with a
neutral and a high-stakes note. Reports:
- agreement with the person's labels and Cohen's kappa ("Not sure" labels left out);
- how often the high-stakes note changed the verdict (dev plan 9.4: it must not);
- agreement with how each item was built, as a check on the set itself.

Live mode uses the router's `judge` route (local mode: the grader model). With
AXIOM_RECORD=1 the answers are saved and later runs can replay them in demo mode.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

from grader import (  # noqa: E402
    VERSIONS,
    CitationItem,
    FindingItem,
    Grade,
    Source,
    Stakes,
    grade_citation,
    grade_finding,
)

from axiom.agents.base import AgentContext  # noqa: E402
from axiom.jobs.worker import build_context  # noqa: E402
from axiom.mcp_clients.evidence import InMemoryEvidence  # noqa: E402
from axiom.router.router import ModelRouter  # noqa: E402
from axiom.settings import REPO_ROOT, Settings  # noqa: E402
from axiom.tools.policy import load_policy  # noqa: E402

SET = HERE / "v1"
POSITIVE = {"supported", "covered"}
ALTERED = {"changed_number", "wrong_line", "unsupported_clause"}


def load_items() -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = json.loads((SET / "calibration.json").read_text())["items"]
    return items


def load_labels(path: Path = SET / "labels.json") -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    labels: dict[str, dict[str, Any]] = json.loads(path.read_text())["labels"]
    return labels


def context(mode: str) -> AgentContext:
    if mode == "live":
        settings = Settings.from_env()
        if settings.mode == "demo":
            raise SystemExit("live mode needs AXIOM_MODE=local and model keys in the environment")
        return build_context(settings)
    settings = Settings.from_env()
    settings = replace(settings, recordings_override=REPO_ROOT / "data" / "recorded")
    return AgentContext(
        ModelRouter.from_settings(settings.config_dir, "demo", settings.recordings_dir),
        InMemoryEvidence(settings.reference_dirs),
        load_policy(settings.config_dir / "policy_thresholds.yaml"),
        settings.prompts_dir,
    )


async def grade(ctx: AgentContext, item: dict[str, Any], stakes: Stakes) -> Grade:
    if item["type"] == "citation":
        return await grade_citation(
            ctx,
            CitationItem(
                item["item_id"], item["claim"], tuple(Source(**s) for s in item["sources"])
            ),
            stakes,
        )
    return await grade_finding(
        ctx, FindingItem(item["item_id"], item["finding"], tuple(item["memo"])), stakes
    )


def expected_from_construction(item: dict[str, Any]) -> bool | None:
    """What the build implies: an altered claim or another package's finding should fail.
    An unaltered claim or the package's own finding may pass or fail; it implies nothing."""
    if item["built_as"] in ALTERED or item["built_as"].startswith("finding_from_"):
        return False
    return None


def kappa(pairs: list[tuple[bool, bool]]) -> float | None:
    """Cohen's kappa for two raters on a yes/no label."""
    n = len(pairs)
    if not n:
        return None
    agree = sum(a == b for a, b in pairs) / n
    pa, pb = sum(a for a, _ in pairs) / n, sum(b for _, b in pairs) / n
    chance = pa * pb + (1 - pa) * (1 - pb)
    return None if chance == 1 else round((agree - chance) / (1 - chance), 4)


def analyse(
    items: list[dict[str, Any]],
    grades: dict[str, dict[str, Grade]],
    labels: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    rows, human_pairs, flips, built_pairs, evaluated = [], [], 0, [], 0
    for item in items:
        g = grades[item["item_id"]]
        neutral, high = g["neutral"].verdict, g["high"].verdict
        label = (labels.get(item["item_id"]) or {}).get("verdict")
        if neutral is not None and high is not None:
            evaluated += 1
            flips += neutral != high
        if neutral is not None and label in (*POSITIVE, "not_supported", "not_covered"):
            human_pairs.append((neutral in POSITIVE, label in POSITIVE))
        implied = expected_from_construction(item)
        if neutral is not None and implied is not None:
            built_pairs.append((neutral in POSITIVE, implied))
        rows.append(
            {
                "item_id": item["item_id"],
                "type": item["type"],
                "label": label,
                "grader_neutral": neutral,
                "grader_high_stakes": high,
                "reason_neutral": g["neutral"].reason,
                "built_as": item["built_as"],
            }
        )
    summary = {
        "items": len(items),
        "graded_both_framings": evaluated,
        "labelled": sum(1 for r in rows if r["label"]),
        "labelled_not_sure": sum(1 for r in rows if r["label"] == "unsure"),
        "agreement_with_labels": round(sum(a == b for a, b in human_pairs) / len(human_pairs), 4)
        if human_pairs
        else None,
        "kappa_with_labels": kappa(human_pairs),
        "compared_with_labels": len(human_pairs),
        "stakes_flip_rate": round(flips / evaluated, 4) if evaluated else None,
        "catches_altered_items": round(sum(a == b for a, b in built_pairs) / len(built_pairs), 4)
        if built_pairs
        else None,
        "altered_items_graded": len(built_pairs),
        "prompt_versions": VERSIONS,
    }
    return {"summary": summary, "rows": rows}


def markdown(report: dict[str, Any], mode: str) -> str:
    s = report["summary"]

    def pct(v: float | None) -> str:
        return "-" if v is None else f"{v:.0%}"

    lines = [
        f"# Grader calibration ({mode} mode)",
        "",
        "Generated by `evals/judge/calibrate.py`. Agreement leaves out items labelled Not sure.",
        "",
        "| Measure | Result |",
        "|---|---|",
        f"| Items graded in both framings | {s['graded_both_framings']} of {s['items']} |",
        f"| Labelled by a person | {s['labelled']} ({s['labelled_not_sure']} not sure) |",
        f"| Agreement with the person's labels | {pct(s['agreement_with_labels'])} "
        f"(n={s['compared_with_labels']}) |",
        f"| Cohen's kappa with the labels | {s['kappa_with_labels'] or '-'} |",
        f"| Verdicts changed by the high-stakes note | {pct(s['stakes_flip_rate'])} |",
        f"| Altered items the grader failed | {pct(s['catches_altered_items'])} "
        f"(n={s['altered_items_graded']}) |",
        "",
        "| Item | Type | Person | Grader | Grader, high stakes | Grader's reason |",
        "|---|---|---|---|---|---|",
    ]
    for r in report["rows"]:
        lines.append(
            f"| {r['item_id']} | {r['type']} | {r['label'] or '-'} "
            f"| {r['grader_neutral'] or 'not evaluated'} "
            f"| {r['grader_high_stakes'] or 'not evaluated'} "
            f"| {r['reason_neutral'].replace('|', '/')} |"
        )
    return "\n".join(lines) + "\n"


async def run(mode: str) -> dict[str, Any]:
    ctx, items = context(mode), load_items()
    grades = {i["item_id"]: {s: await grade(ctx, i, s) for s in ("neutral", "high")} for i in items}
    report = analyse(items, grades, load_labels())
    report["mode"] = mode
    report["grades"] = {k: {s: asdict(g) for s, g in v.items()} for k, v in grades.items()}
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--mode", choices=("demo", "live"), default="demo")
    parser.add_argument("--mlflow", action="store_true")
    args = parser.parse_args(argv)
    started = time.time()
    report = asyncio.run(run(args.mode))
    out = SET / f"calibration-{args.mode}"
    out.with_suffix(".json").write_text(json.dumps(report, indent=2, default=str) + "\n")
    out.with_suffix(".md").write_text(markdown(report, args.mode))
    print(json.dumps(report["summary"]))
    if args.mlflow:
        import mlflow

        mlflow.set_experiment("axiom-judge-calibration")
        with mlflow.start_run(run_name=f"calibration-{args.mode}"):
            mlflow.log_params(
                {"mode": args.mode, **{f"prompt.{k}": v for k, v in VERSIONS.items()}}
            )
            mlflow.log_metrics(
                {k: float(v) for k, v in report["summary"].items() if isinstance(v, (int, float))}
            )
            mlflow.log_metric("duration_s", time.time() - started)
            mlflow.log_dict(report, "calibration.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
