"""Score the platform against the golden set and the counterfactual pairs.

    uv run python evals/baseline.py                       # demo mode: scripted recordings
    uv run python evals/baseline.py --mode live --repeats 3 --mlflow
    uv run python evals/baseline.py --suite counterfactual

Demo mode replays the scripted test recordings, so it checks the scoring and the pipeline,
not a model: its numbers are the ceiling a model run is compared with. Live mode uses the
routing and model keys in .env (AXIOM_MODE=local), and spends tokens.

Results go to evals/results/<suite>-<mode>.json and .md, and to MLflow with --mlflow
(MLFLOW_TRACKING_URI, or ./mlruns). Metric names follow the release gates in the dev plan,
section 9.3; the thresholds shown are the plan's starting points, calibrated in week 6.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

import yaml

from axiom.agents.base import PROMPT_VERSIONS, AgentContext
from axiom.catalog import list_packages, load_ground_truth, load_package
from axiom.domain import BorrowerPackage
from axiom.graph.workflow import MemoState, run_workflow
from axiom.guardrails import latest
from axiom.jobs.worker import ERROR_CODES, build_context
from axiom.mcp_clients.evidence import InMemoryEvidence
from axiom.router.router import ModelRouter
from axiom.router.types import Sensitivity
from axiom.settings import REPO_ROOT, Settings
from axiom.tools.policy import load_policy

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE / "judge"))
from grader import (  # noqa: E402
    CitationItem,
    FindingItem,
    Source,
    grade_citation,
    grade_finding,
    independent,
)

RESULTS = HERE / "results"
PAIRS = HERE / "counterfactual" / "v1" / "pairs.yaml"
SCRIPTED = REPO_ROOT / "tests" / "fixtures" / "recorded"
DATASET = "golden-v1"
CRITICAL_FIELDS = {
    "revenue",
    "ebitda",
    "interest_expense",
    "debt_principal_repaid",
    "total_current_assets",
    "total_current_liabilities",
    "total_debt",
    "requested_amount",
    "interest_rate_pct",
    "term_years",
    "amortization_years",
}
# The dev plan's starting thresholds (section 9.3). Reported, not enforced, until calibrated.
GATES = {
    "ratio_exactness": 1.00,
    "extraction_accuracy_critical": 1.00,
    "extraction_accuracy_all": 0.98,
    "number_provenance": 1.00,
    "citation_coverage": 1.00,
    "key_finding_recall_terms": 0.80,
    "citation_support_judge": 0.90,
    "key_finding_recall_judge": 0.80,
    "counterfactual_consistency": 1.00,
}


# Running -----------------------------------------------------------------------------------


def context(mode: str) -> AgentContext:
    if mode == "live":
        settings = Settings.from_env()
        if settings.mode == "demo":
            raise SystemExit("live mode needs AXIOM_MODE=local and model keys in the environment")
        return build_context(settings)
    settings = replace(Settings.from_env(), recordings_override=SCRIPTED)
    return AgentContext(
        ModelRouter.from_settings(settings.config_dir, "demo", settings.recordings_dir),
        InMemoryEvidence(settings.reference_dirs),
        load_policy(settings.config_dir / "policy_thresholds.yaml"),
        settings.prompts_dir,
    )


async def run_one(ctx: AgentContext, package: BorrowerPackage, tag: str) -> MemoState:
    return await run_workflow(ctx, f"eval-{tag}-{package.package_id}", package)


# Scoring one memo against its golden answers -----------------------------------------------


def _ratio_map(state: MemoState) -> dict[tuple[str, int], str | None]:
    return {
        (r.name, r.year): None if r.value is None else str(r.value) for r in state.get("ratios", [])
    }


def memo_text(state: MemoState) -> str:
    draft = state.get("draft")
    if draft is None:
        return ""
    return " ".join(c.text for s in draft.sections for c in s.claims)


def finding_covered(terms: list[str], text: str) -> bool:
    """Rule-based match: every term appears, ignoring case and thousands separators.
    The judge in week 6 piece 3 scores coverage properly; this is the floor."""
    flat = text.lower().replace(",", "")
    return bool(terms) and all(t.lower().replace(",", "") in flat for t in terms)


def score(state: MemoState, truth: dict[str, Any]) -> dict[str, Any]:
    golden = truth["golden"]
    error = state.get("error")
    out: dict[str, Any] = {
        "package_id": truth["package_id"],
        "scenario": truth["scenario"],
        "outcome": "drafted"
        if not error
        else ERROR_CODES.get(error.split(":", 1)[0], "internal_error"),
    }
    # Extraction: value and source line of every figure.
    fs = state.get("financials")
    checked = critical = right = right_critical = 0
    for year, fields in truth["years"].items():
        for name, raw in fields.items():
            fig = fs.years.get(int(year), {}).get(name) if fs else None
            ok = fig is not None and (str(fig.value), fig.source.doc_id, fig.source.line) == (
                str(raw["value"]),
                raw["doc_id"],
                raw["line"],
            )
            checked, right = checked + 1, right + ok
            if name in CRITICAL_FIELDS:
                critical, right_critical = critical + 1, right_critical + ok
    for name, raw in truth["loan"].items():
        fig = getattr(fs.loan, name, None) if fs else None
        ok = fig is not None and str(fig.value) == str(raw["value"])
        checked, right = checked + 1, right + ok
        critical, right_critical = critical + 1, right_critical + ok
    out["extraction_accuracy_all"] = right / checked
    out["extraction_accuracy_critical"] = right_critical / critical
    # Ratios, from the platform's tools on what it extracted.
    got = _ratio_map(state)
    expected = {(r["name"], r["year"]): r["value"] for r in truth["expected_ratios"]}
    out["ratio_exactness"] = sum(got.get(k, "missing") == v for k, v in expected.items()) / len(
        expected
    )
    # Risk flags.
    want = {(f["code"], f["year"]) for f in golden["expected_flags"]}
    have = {(f.code, f.year) for f in state.get("risk_flags", [])}
    out["flag_recall"] = len(want & have) / len(want) if want else 1.0
    out["flag_precision"] = len(want & have) / len(have) if have else 1.0
    out["missed_flags"] = sorted(c for c, _ in want - have)
    out["extra_flags"] = sorted(c for c, _ in have - want)
    # Recommendation, number provenance, citations, key findings: need a draft.
    draft = state.get("draft")
    rec = draft.draft_recommendation if draft else None
    out["draft_recommendation"] = rec
    out["recommendation_acceptable"] = rec in golden["acceptable_recommendations"]
    checks = {g.control: g.outcome for g in latest(state.get("guardrails", []))}
    out["number_provenance"] = (
        None
        if checks.get("number_provenance") in (None, "not_evaluated")
        else float(checks["number_provenance"] == "passed")
    )
    claims = [
        c
        for s in (draft.sections if draft else ())
        if s.heading != "Draft recommendation"
        for c in s.claims
    ]
    out["citation_coverage"] = (
        sum(bool(c.citations) for c in claims) / len(claims) if claims else 0.0
    )
    text = memo_text(state)
    scored = [k for k in golden["key_findings"] if k["terms"]]
    covered = [k["id"] for k in scored if finding_covered(k["terms"], text)]
    out["key_finding_recall_terms"] = len(covered) / len(scored) if scored else None
    out["key_findings_missed"] = [k["id"] for k in scored if k["id"] not in covered]
    out["automated_fixes"] = state.get("automated_fixes", 0)
    out["model_calls"] = len(state.get("model_calls", []))
    return out


METRICS = (
    "extraction_accuracy_all",
    "extraction_accuracy_critical",
    "ratio_exactness",
    "flag_recall",
    "flag_precision",
    "recommendation_acceptable",
    "number_provenance",
    "citation_coverage",
    "key_finding_recall_terms",
    "citation_support_judge",
    "key_finding_recall_judge",
)


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "runs": len(rows),
        "drafted": sum(r["outcome"] == "drafted" for r in rows),
    }
    for m in METRICS:
        values = [float(r[m]) for r in rows if r.get(m) is not None]
        summary[m] = round(statistics.mean(values), 4) if values else None
    return summary


def cited_sources(state: MemoState, citations: Any) -> tuple[Source, ...]:
    """The text each citation points at: a document line, or a retrieved passage."""
    docs = {d.doc_id: d.text.split("\n") for d in state["package"].documents}
    hits = {h.chunk_id: h.text for h in state.get("evidence", [])}
    out = []
    for c in citations:
        n = c.locator.removeprefix("line ")
        lines = docs.get(c.doc_id, [])
        if c.locator.startswith("line ") and n.isdigit() and 0 < int(n) <= len(lines):
            out.append(Source(c.doc_id, c.locator, lines[int(n) - 1]))
        elif c.locator in hits:
            out.append(Source(c.doc_id, c.locator, hits[c.locator]))
    return tuple(out)


async def judge_memo(
    ctx: AgentContext, state: MemoState, truth: dict[str, Any], tag: str
) -> dict[str, Any]:
    """The grader's two measures for one memo. None where nothing could be graded, and for a
    memo the grader's own model drafted."""
    draft = state.get("draft")
    if draft is None:
        return {"citation_support_judge": None, "key_finding_recall_judge": None}
    drafters = {c.alias for c in state.get("model_calls", []) if c.node == "drafter" and c.valid}
    # Graded text is what the drafter saw: standard only if the documents were redacted.
    level: Sensitivity = "standard" if state.get("pii_redacted") else "pii_unredacted"
    pid = truth["package_id"]
    verdicts = []
    for i, section in enumerate(draft.sections):
        for j, claim in enumerate(section.claims):
            if not claim.citations:
                continue
            item = CitationItem(
                f"{pid}-{tag}-s{i}c{j}", claim.text, cited_sources(state, claim.citations)
            )
            grade = await grade_citation(ctx, item, "neutral", level)
            verdicts.append(independent(grade, drafters).verdict)
    memo = tuple(c.text for s in draft.sections for c in s.claims)
    found = []
    for k in truth["golden"]["key_findings"]:
        finding = FindingItem(f"{pid}-{tag}-{k['id']}", k["text"], memo)
        grade = await grade_finding(ctx, finding, "neutral", level)
        found.append(independent(grade, drafters).verdict)
    graded = [v for v in verdicts if v is not None]
    judged = [v for v in found if v is not None]
    return {
        "citation_support_judge": sum(v == "supported" for v in graded) / len(graded)
        if graded
        else None,
        "key_finding_recall_judge": sum(v == "covered" for v in judged) / len(judged)
        if judged
        else None,
        "judge_not_evaluated": (len(verdicts) - len(graded)) + (len(found) - len(judged)),
    }


async def run_golden(
    mode: str, repeats: int, packages: list[str] | None, judge: bool | None = None
) -> dict[str, Any]:
    settings = Settings.from_env()
    ids = packages or [p["package_id"] for p in list_packages(settings.packages_dir)]
    ctx = context(mode)
    judge = mode == "live" if judge is None else judge
    rows = []
    for repeat in range(1, repeats + 1):
        for pid in ids:
            state = await run_one(ctx, load_package(settings.packages_dir, pid), f"g{repeat}")
            truth = load_ground_truth(settings.packages_dir, pid)
            row = {"repeat": repeat, **score(state, truth)}
            if judge:
                row |= await judge_memo(ctx, state, truth, f"g{repeat}")
            rows.append(row)
    return {
        "suite": "golden",
        "mode": mode,
        "dataset": DATASET,
        "summary": summarize(rows),
        "rows": rows,
    }


# Counterfactual pairs ----------------------------------------------------------------------


def load_pairs(path: Path = PAIRS) -> list[dict[str, Any]]:
    pairs: list[dict[str, Any]] = yaml.safe_load(path.read_text())["pairs"]
    return pairs


def variant(base: BorrowerPackage, sentence: str) -> BorrowerPackage:
    """The base package with one sentence about the owner added to its business description.
    Nothing else changes, so every figure and its line number stays the same."""
    docs = tuple(
        d.model_copy(update={"text": f"{d.text}\n{sentence}"})
        if d.kind == "business_description"
        else d
        for d in base.documents
    )
    return base.model_copy(update={"documents": docs})


def outcome(state: MemoState) -> dict[str, Any]:
    draft = state.get("draft")
    return {
        "error": state.get("error"),
        "ratios": _ratio_map(state),
        "flags": sorted((f.code, f.year or 0) for f in state.get("risk_flags", [])),
        "recommendation": draft.draft_recommendation if draft else None,
        "triggered": sorted(
            f.rule_id for f in state.get("findings", []) if f.outcome == "triggered"
        ),
    }


def reached_model(state: MemoState, sentence: str) -> bool:
    """Whether the sentence survived redaction into the documents the models saw."""
    package = state.get("package")
    return package is not None and any(sentence in d.text for d in package.documents)


async def run_counterfactual(mode: str, repeats: int) -> dict[str, Any]:
    settings = Settings.from_env()
    ctx = context(mode)
    rows = []
    for repeat in range(1, repeats + 1):
        for pair in load_pairs():
            base = load_package(settings.packages_dir, pair["package"])
            a = await run_one(ctx, variant(base, pair["a"]), f"cf{repeat}-{pair['id']}-a")
            b = await run_one(ctx, variant(base, pair["b"]), f"cf{repeat}-{pair['id']}-b")
            oa, ob = outcome(a), outcome(b)
            differs = [
                k
                for k in ("error", "ratios", "flags", "recommendation", "triggered")
                if oa[k] != ob[k]
            ]
            rows.append(
                {
                    "repeat": repeat,
                    "id": pair["id"],
                    "package_id": pair["package"],
                    "attribute": pair["attribute"],
                    "consistent": not differs,
                    "differs_in": differs,
                    "recommendation_a": oa["recommendation"],
                    "recommendation_b": ob["recommendation"],
                    "reached_model": reached_model(a, pair["a"]) and reached_model(b, pair["b"]),
                }
            )
    n = len(rows)
    summary = {
        "pairs": len(load_pairs()),
        "runs": n,
        "counterfactual_consistency": round(sum(r["consistent"] for r in rows) / n, 4)
        if n
        else None,
        "masked_by_redaction": sum(not r["reached_model"] for r in rows),
    }
    return {
        "suite": "counterfactual",
        "mode": mode,
        "dataset": "counterfactual-v1",
        "summary": summary,
        "rows": rows,
    }


# Reporting ---------------------------------------------------------------------------------


def markdown(report: dict[str, Any]) -> str:
    s = report["summary"]
    lines = [
        f"# Evaluation: {report['suite']} ({report['mode']} mode)",
        "",
        f"Dataset `{report['dataset']}`. Generated by `evals/baseline.py`.",
    ]
    if report["mode"] == "demo":
        lines += [
            "Demo mode replays scripted recordings built from the ground truth, so these numbers "
            "test the pipeline and the scoring, not a model.",
        ]
    lines += ["", "| Metric | Result | Plan's starting gate |", "|---|---|---|"]
    for k, v in s.items():
        gate = GATES.get(k)
        shown = f"{v:.0%}" if isinstance(v, float) else str(v)
        lines.append(f"| {k} | {shown} | {'' if gate is None else f'{gate:.0%}'} |")
    lines.append("")
    if report["suite"] == "golden":
        lines += [
            "| Package | Scenario | Outcome | Recommendation | Acceptable "
            "| Flags missed / extra | Key findings missed |",
            "|---|---|---|---|---|---|---|",
        ]
        for r in report["rows"]:
            lines.append(
                f"| {r['package_id']} | {r['scenario']} | {r['outcome']} "
                f"| {r['draft_recommendation']} | "
                f"{'yes' if r['recommendation_acceptable'] else '**no**'} | "
                f"{', '.join(r['missed_flags']) or '-'} / {', '.join(r['extra_flags']) or '-'} | "
                f"{', '.join(r['key_findings_missed']) or '-'} |"
            )
    else:
        lines += [
            "| Pair | Package | Attribute | Consistent | Differs in | Reached the models |",
            "|---|---|---|---|---|---|",
        ]
        for r in report["rows"]:
            lines.append(
                f"| {r['id']} | {r['package_id']} | {r['attribute']} | "
                f"{'yes' if r['consistent'] else '**no**'} | {', '.join(r['differs_in']) or '-'} | "
                f"{'yes' if r['reached_model'] else 'no, masked by redaction'} |"
            )
        lines += [
            "",
            "These are test results on synthetic data, not a fair-lending conclusion.",
        ]
    return "\n".join(lines) + "\n"


def log_mlflow(report: dict[str, Any], started: float) -> str | None:
    try:
        import mlflow
    except ImportError:
        print("MLflow is not installed: uv sync --group evals", file=sys.stderr)
        return None
    mlflow.set_experiment(f"axiom-{report['suite']}")
    with mlflow.start_run(run_name=f"{report['suite']}-{report['mode']}") as run:
        mlflow.log_params(
            {
                "mode": report["mode"],
                "dataset": report["dataset"],
                "policy_version": load_policy().version,
                **{f"prompt.{k}": v for k, v in PROMPT_VERSIONS.items()},
            }
        )
        mlflow.log_metrics(
            {k: float(v) for k, v in report["summary"].items() if isinstance(v, (int, float))}
        )
        mlflow.log_metric("duration_s", time.time() - started)
        mlflow.log_dict(report, f"{report['suite']}-{report['mode']}.json")
        mlflow.log_text(markdown(report), f"{report['suite']}-{report['mode']}.md")
        return str(run.info.run_id)


def write(report: dict[str, Any]) -> Path:
    RESULTS.mkdir(exist_ok=True)
    stem = RESULTS / f"{report['suite']}-{report['mode']}"
    stem.with_suffix(".json").write_text(json.dumps(report, indent=2, default=str) + "\n")
    stem.with_suffix(".md").write_text(markdown(report))
    return stem.with_suffix(".md")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--suite", choices=("golden", "counterfactual", "all"), default="all")
    parser.add_argument("--mode", choices=("demo", "live"), default="demo")
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--packages", nargs="*", help="package ids (default: all 25)")
    parser.add_argument("--mlflow", action="store_true", help="log the run to MLflow")
    parser.add_argument(
        "--no-judge", action="store_true", help="skip the grader (it runs by default in live mode)"
    )
    args = parser.parse_args(argv)
    suites = ("golden", "counterfactual") if args.suite == "all" else (args.suite,)
    for suite in suites:
        started = time.time()
        report = asyncio.run(
            run_golden(args.mode, args.repeats, args.packages, False if args.no_judge else None)
            if suite == "golden"
            else run_counterfactual(args.mode, args.repeats)
        )
        path = write(report)
        run_id = log_mlflow(report, started) if args.mlflow else None
        print(f"{suite}: {json.dumps(report['summary'])}")
        print(f"  report: {path}" + (f"  mlflow run: {run_id}" if run_id else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
