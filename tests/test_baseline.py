"""The evaluation runner: scoring rules, the demo baseline, and the committed results."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "evals"))
import baseline

from axiom.catalog import load_ground_truth
from conftest import SETTINGS

KNOWN_GAPS = {"pkg-21", "pkg-23"}  # drafting ignores missing documents and loan scope


@pytest.fixture(scope="module")
def golden() -> dict[str, Any]:
    return asyncio.run(baseline.run_golden("demo", 1, None))


@pytest.fixture(scope="module")
def counterfactual() -> dict[str, Any]:
    return asyncio.run(baseline.run_counterfactual("demo", 1))


def test_terms_match_ignoring_case_and_thousands_separators() -> None:
    assert baseline.finding_covered(["DSCR", "1.24"], "Pro forma dscr is 1.24.")
    assert baseline.finding_covered(["total assets", "50000"], "Total assets differ by 50,000.")
    assert not baseline.finding_covered(["DSCR", "1.24"], "Pro forma DSCR is 1.25.")
    assert not baseline.finding_covered([], "anything")


def test_a_failed_job_scores_zero_not_a_pass() -> None:
    truth = load_ground_truth(SETTINGS.packages_dir, "pkg-01")
    row = baseline.score({"error": "DocumentQuarantined: x", "guardrails": []}, truth)  # type: ignore[typeddict-item]
    assert row["outcome"] == "document_quarantined"
    assert row["extraction_accuracy_all"] == 0 and row["ratio_exactness"] == 0
    assert row["recommendation_acceptable"] is False and row["number_provenance"] is None


def test_demo_baseline_scores_the_pipeline_at_its_ceiling(golden: dict[str, Any]) -> None:
    s = golden["summary"]
    assert s["runs"] == s["drafted"] == 25
    for metric in (
        "extraction_accuracy_all",
        "extraction_accuracy_critical",
        "ratio_exactness",
        "flag_recall",
        "flag_precision",
        "number_provenance",
        "citation_coverage",
    ):
        assert s[metric] == 1.0, metric


def test_demo_baseline_reports_the_known_recommendation_gaps(golden: dict[str, Any]) -> None:
    """If drafting starts honouring the rule engine, update KNOWN_GAPS and the results."""
    wrong = {r["package_id"] for r in golden["rows"] if not r["recommendation_acceptable"]}
    assert wrong == KNOWN_GAPS


def test_every_pair_is_consistent_and_name_pairs_are_masked(counterfactual: dict[str, Any]) -> None:
    s = counterfactual["summary"]
    assert s["pairs"] == len(baseline.load_pairs()) >= 20
    assert s["counterfactual_consistency"] == 1.0
    masked = {r["id"] for r in counterfactual["rows"] if not r["reached_model"]}
    assert masked == {r["id"] for r in counterfactual["rows"] if r["attribute"] == "race (by name)"}


def test_pairs_change_only_the_business_description() -> None:
    from axiom.catalog import load_package

    for pair in baseline.load_pairs():
        base = load_package(SETTINGS.packages_dir, pair["package"])
        a, b = baseline.variant(base, pair["a"]), baseline.variant(base, pair["b"])
        for da, db, d0 in zip(a.documents, b.documents, base.documents, strict=True):
            if d0.kind != "business_description":
                assert da.text == db.text == d0.text
        assert pair["a"] != pair["b"]


@pytest.mark.parametrize("suite", ["golden", "counterfactual"])
def test_the_committed_demo_results_match_a_fresh_run(
    suite: str, golden: dict[str, Any], counterfactual: dict[str, Any]
) -> None:
    fresh = golden if suite == "golden" else counterfactual
    committed = json.loads((baseline.RESULTS / f"{suite}-demo.json").read_text())
    assert committed == json.loads(json.dumps(fresh, default=str)), (
        "run: uv run python evals/baseline.py"
    )
