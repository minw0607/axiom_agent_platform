"""Score model documentation review (app 2) against its golden set: six synthetic packages.

    uv run python evals/model_review/review_eval.py               # demo mode: scripted answers
    uv run python evals/model_review/review_eval.py --mode live --repeats 3   # spends tokens

Each package's ground_truth.json is its answer key: which checklist sections are missing or
empty (decided by code, so any miss is a bug), and which findings a careful reviewer would
make, including the present sections that are weak (the model's judgment).

Demo mode replays the scripted responses in tests/fixtures/recorded/model_review/
(scripts/seed_model_review.py), which answer from the ground truth: its results test the
pipeline and the scoring, not a model, and are the ceiling a live run is compared with.
Live mode uses the routing and model keys in the environment (AXIOM_MODE=local).

Results go to evals/model_review/results/golden-<mode>.json and .md. The release gates in
config/eval_gates.yaml (suite model_review_golden) apply to these rows.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import statistics
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

from axiom.apps.model_review import APP
from axiom.apps.model_review.agents import ReviewContext
from axiom.apps.model_review.catalog import list_packages, load_ground_truth, load_package
from axiom.apps.model_review.catalog import packages_dir as model_docs_dir
from axiom.apps.model_review.domain import DocumentationPackage
from axiom.apps.model_review.graph import ReviewState, run_workflow
from axiom.apps.model_review.tools.checklist import load_checklist
from axiom.guardrails import latest
from axiom.settings import REPO_ROOT, Settings

HERE = Path(__file__).parent
RESULTS = HERE / "results"
SCRIPTED = REPO_ROOT / "tests" / "fixtures" / "recorded"
DATASET = "model-review-golden-v1"
# Words too common in findings to show that one was made.
STOPWORDS = {
    "about",
    "although",
    "because",
    "every",
    "model",
    "section",
    "there",
    "their",
    "these",
    "which",
    "without",
}
COVERED = 0.5  # share of a finding's terms the report must use for the finding to count
METRICS = (
    "job_completion",
    "gaps_exact",
    "critical_gaps_exact",
    "completeness_exact",
    "gaps_named",
    "citations_valid",
    "no_decision_stated",
    "status_matches",
    "weak_recall",
    "adequate_kept",
    "finding_coverage_terms",
)


def context(mode: str) -> ReviewContext:
    settings = Settings.from_env()
    if mode == "live":
        if settings.mode == "demo":
            raise SystemExit("live mode needs AXIOM_MODE=local and model keys in the environment")
        return APP.build_context(settings)
    # The scripted answers, with no governance library: the provisions step records
    # not_evaluated, and the report cites the checklist's own provisions.
    settings = replace(
        settings,
        mode="demo",
        recordings_override=SCRIPTED,
        tool_gateway_url=None,
        regulus_mcp_url=None,
    )
    return APP.build_context(settings)


def expected_status(truth: dict[str, Any], weak: set[str]) -> str:
    """What the documentation's state calls for, by the same rule code applies."""
    if truth["critical_gaps"]:
        return "major_gaps"
    gaps = any(c["outcome"] != "present" for c in truth["checks"].values())
    return "remediation_needed" if gaps or weak else "no_material_gaps"


def expected_weak(truth: dict[str, Any]) -> set[str]:
    """Present sections the ground truth expects a finding on: the model should call them weak."""
    return {i for i in truth["expected_findings"] if truth["checks"][i]["outcome"] == "present"}


def terms(text: str) -> set[str]:
    words = {w.lower() for w in re.findall(r"[A-Za-z][A-Za-z-]{4,}", text)}
    return words - STOPWORDS


def finding_covered(finding: str, report_text: str) -> bool:
    wanted = terms(finding)
    have = terms(report_text)
    return bool(wanted) and len(wanted & have) / len(wanted) >= COVERED


def _citations_valid(state: ReviewState, package: DocumentationPackage) -> bool:
    """Every citation is a line of a submitted document, or a checklist provision."""
    lines = {d.doc_id: len(d.text.splitlines()) for d in package.documents}
    provisions = {
        (p.framework, p.provision_id) for item in APP_CHECKLIST.items for p in item.provisions
    }
    for section in state["report"].sections:
        for claim in section.claims:
            for c in claim.citations:
                if c.doc_id in lines:
                    m = re.fullmatch(r"line (\d+)", c.locator)
                    if not m or not 1 <= int(m.group(1)) <= lines[c.doc_id]:
                        return False
                elif (c.doc_id, c.locator) not in provisions:
                    return False
    return True


def score(
    state: ReviewState, truth: dict[str, Any], package: DocumentationPackage
) -> dict[str, Any]:
    done = bool(state.get("report")) and not state.get("error")
    row: dict[str, Any] = {
        "package_id": truth["package_id"],
        "scenario": truth["scenario"],
        "outcome": "drafted" if done else "failed",
        "job_completion": done,
    }
    if not done:
        rails = sorted({r.control for r in state.get("guardrails", []) if r.outcome == "triggered"})
        row["error"] = (state.get("error") or "no report").split(":", 1)[0]
        row["guardrails_triggered"] = rails
        return row
    checks = {c.item_id: c.outcome for c in state["checks"]}
    gaps = {i: o for i, o in checks.items() if o != "present"}
    want_gaps = {i: c["outcome"] for i, c in truth["checks"].items() if c["outcome"] != "present"}
    completeness = state["completeness"]
    weak_want = expected_weak(truth)
    assessed = {a.item_id: a.adequacy for a in state["assessments"]}
    weak_got = {i for i, a in assessed.items() if a == "weak"}
    adequate_want = {i for i in assessed if i not in weak_want}
    report_text = " ".join(claim.text for s in state["report"].sections for claim in s.claims)
    by_heading = {s.heading: " ".join(c.text for c in s.claims) for s in state["report"].sections}
    findings = truth["expected_findings"]
    decision = [r for r in latest(state.get("guardrails", [])) if r.control == "decision_language"]
    row |= {
        "gaps_exact": gaps == want_gaps,
        "critical_gaps_exact": list(completeness.critical_gaps) == truth["critical_gaps"],
        "completeness_exact": f"{completeness.present}/{completeness.total}"
        == truth["completeness"],
        "gaps_named": all(i in by_heading.get("Checklist gaps", "") for i in gaps)
        and all(i in by_heading.get("Section assessments", "") for i in weak_got),
        "citations_valid": _citations_valid(state, package),
        "no_decision_stated": bool(decision) and decision[0].outcome == "passed",
        "documentation_status": state["documentation_status"],
        "status_matches": state["documentation_status"] == expected_status(truth, weak_want),
        "weak_expected": sorted(weak_want),
        "weak_found": sorted(weak_got),
        "weak_recall": len(weak_got & weak_want) / len(weak_want) if weak_want else None,
        "adequate_kept": len(adequate_want - weak_got) / len(adequate_want)
        if adequate_want
        else None,
        "finding_coverage_terms": statistics.mean(
            float(finding_covered(f, report_text)) for f in findings.values()
        )
        if findings
        else None,
        "findings_missed": sorted(
            i for i, f in findings.items() if not finding_covered(f, report_text)
        ),
        "automated_fixes": state.get("automated_fixes", 0),
    }
    return row


APP_CHECKLIST = load_checklist(
    Settings.from_env().app_config_dir("model_review") / "checklist.yaml"
)


async def run_golden(mode: str, repeats: int, packages: list[str] | None) -> dict[str, Any]:
    settings = Settings.from_env()
    folder = model_docs_dir(settings.data_dir)
    ids = packages or [p["package_id"] for p in list_packages(folder)]
    ctx = context(mode)
    rows = []
    for repeat in range(1, repeats + 1):
        for pid in ids:
            package = load_package(folder, pid)
            state = await run_workflow(ctx, f"eval-g{repeat}-{pid}", package)
            rows.append({"repeat": repeat, **score(state, load_ground_truth(folder, pid), package)})
    return {
        "suite": "golden",
        "mode": mode,
        "dataset": DATASET,
        "checklist_version": ctx.checklist.version,
        "summary": summarize(rows),
        "rows": rows,
    }


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {"runs": len(rows)}
    for metric in METRICS:
        values = [r[metric] for r in rows if r.get(metric) is not None]
        out[metric] = round(statistics.mean(float(v) for v in values), 3) if values else None
    return out


def _cell(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "yes" if value else "**no**"
    if isinstance(value, float):
        return f"{value:.2f}"
    if isinstance(value, list):
        return ", ".join(value) or "-"
    return str(value)


def markdown(report: dict[str, Any]) -> str:
    s = report["summary"]
    lines = [
        f"# Model documentation review: golden set, {report['mode']} mode",
        "",
        f"Generated by `evals/model_review/review_eval.py`. Dataset {report['dataset']}, "
        f"checklist {report['checklist_version']}, {s['runs']} runs.",
        "",
    ]
    if report["mode"] == "demo":
        lines += [
            "Demo mode replays scripted answers built from the ground truth, so these numbers "
            "test the pipeline and the scoring, not a model: they are the ceiling a live run "
            "is compared with.",
            "",
        ]
    lines += ["| Metric | Mean over runs |", "|---|---|"]
    lines += [f"| {m} | {_cell(s[m])} |" for m in METRICS]
    lines += [
        "",
        "| Package | Scenario | Status | Matches | Weak expected | Weak found | "
        "Findings covered | Missed |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in report["rows"]:
        if r["outcome"] != "drafted":
            lines.append(
                f"| {r['package_id']} | {r['scenario']} | failed: {r['error']} | | | | | |"
            )
            continue
        lines.append(
            f"| {r['package_id']} | {r['scenario']} | {r['documentation_status']} | "
            f"{_cell(r['status_matches'])} | {_cell(r['weak_expected'])} | "
            f"{_cell(r['weak_found'])} | {_cell(r['finding_coverage_terms'])} | "
            f"{_cell(r['findings_missed'])} |"
        )
    lines += [
        "",
        "Six packages and three weak sections: a small set. It catches a broken prompt; it "
        "cannot estimate accuracy. Findings are matched by their terms, not judged.",
    ]
    return "\n".join(lines) + "\n"


def write(report: dict[str, Any]) -> Path:
    RESULTS.mkdir(exist_ok=True)
    stem = RESULTS / f"{report['suite']}-{report['mode']}"
    stem.with_suffix(".json").write_text(json.dumps(report, indent=2, default=str) + "\n")
    stem.with_suffix(".md").write_text(markdown(report))
    return stem.with_suffix(".md")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--mode", choices=("demo", "live"), default="demo")
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--packages", nargs="*", help="package ids (default: all six)")
    args = parser.parse_args(argv)
    report = asyncio.run(run_golden(args.mode, args.repeats, args.packages))
    path = write(report)
    print(json.dumps(report["summary"]))
    print(f"  report: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
