"""App 2, step 3a: model documentation review's golden set, attack set and release gates.

All keyless: the scripted answers come from the ground truth, and the stub model from the
workflow tests misjudges on purpose to show the scorer notices.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

from axiom.apps.model_review import APP
from axiom.apps.model_review.catalog import load_ground_truth, load_package
from axiom.router.types import ModelRequest
from conftest import SETTINGS
from test_model_review_workflow import (
    FOLDER,
    GOOD_STATUS,
    FakeLibrary,
    Stub,
    careful_assessor,
    careful_drafter,
    context,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "evals"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "evals" / "model_review"))
import gate
import review_attacks
import review_eval

from axiom.apps.model_review.graph import run_workflow

ROOT = Path(__file__).resolve().parents[1]


def _seeder() -> Any:
    spec = importlib.util.spec_from_file_location(
        "seed_model_review", ROOT / "scripts" / "seed_model_review.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_scripted_recordings_are_current(tmp_path: Path) -> None:
    """Reseeding writes the committed recordings byte for byte: a changed prompt, payload or
    scripted answer shows up here, not as a silent replay with prompt_matches False."""
    asyncio.run(_seeder().main(tmp_path, quiet=True))
    committed = review_eval.SCRIPTED / "model_review"
    fresh = sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*.json"))
    assert fresh == sorted(p.relative_to(committed) for p in committed.rglob("*.json"))
    for rel in fresh:
        assert (tmp_path / rel).read_text() == (committed / rel).read_text(), rel


def test_the_committed_demo_results_are_current() -> None:
    fresh = asyncio.run(review_eval.run_golden("demo", 1, None))
    committed = json.loads((review_eval.RESULTS / "golden-demo.json").read_text())
    assert json.loads(json.dumps(fresh, default=str)) == committed


def test_the_scripted_answers_reach_the_ceiling_and_the_expected_status() -> None:
    report = asyncio.run(review_eval.run_golden("demo", 1, None))
    assert {r["package_id"]: r["documentation_status"] for r in report["rows"]} == GOOD_STATUS
    assert all(v == 1.0 for k, v in report["summary"].items() if k != "runs")
    # Four present-but-weak sections in the set: the model's real judgment.
    weak = {r["package_id"]: r["weak_expected"] for r in report["rows"] if r["weak_expected"]}
    assert weak == {"mdl-04": ["MR-4"], "mdl-05": ["MR-6"], "mdl-06": ["MR-4", "MR-6"]}


def score(package_id: str, stub: Stub) -> dict[str, Any]:
    package = load_package(FOLDER, package_id)
    state = asyncio.run(run_workflow(context(stub, FakeLibrary()), f"job-{package_id}", package))
    return review_eval.score(state, load_ground_truth(FOLDER, package_id), package)


def lenient(request: ModelRequest, data: dict[str, Any]) -> dict[str, Any]:
    """An assessor that calls every section adequate."""
    out = careful_assessor(request, data)
    for a in out["assessments"]:
        a["adequacy"] = "adequate"
    return out


def harsh(request: ModelRequest, data: dict[str, Any]) -> dict[str, Any]:
    """An assessor that calls every section weak."""
    out = careful_assessor(request, data)
    for a in out["assessments"]:
        a["adequacy"] = "weak"
    return out


def test_a_lenient_model_misses_the_weak_section_and_the_status() -> None:
    row = score("mdl-04", Stub({"assessor": lenient, "drafter": careful_drafter}))
    assert row["job_completion"] and row["gaps_exact"]  # code's part is still exact
    assert row["weak_recall"] == 0.0 and row["adequate_kept"] == 1.0
    # MR-5 is still a gap, so the status holds; the weak finding on MR-4 is not covered.
    assert row["status_matches"] and "MR-4" in row["findings_missed"]


def test_a_harsh_model_loses_the_complete_package() -> None:
    row = score("mdl-01", Stub({"assessor": harsh, "drafter": careful_drafter}))
    assert row["documentation_status"] == "remediation_needed" and not row["status_matches"]
    assert row["adequate_kept"] == 0.0 and row["weak_recall"] is None
    # The model's reason for every weak call is kept, so a live run's misses can be read.
    assert set(row["weak_reasons"]) == set(row["weak_found"]) and all(row["weak_reasons"].values())


def test_a_failed_job_is_scored_as_not_completed() -> None:
    row = score("mdl-02", Stub({"assessor": lambda r, d: None, "drafter": careful_drafter}))
    assert row == {
        "package_id": "mdl-02",
        "scenario": "no_monitoring_metrics",
        "outcome": "failed",
        "job_completion": False,
        "error": row["error"],
        "guardrails_triggered": [],
        "guardrail_reasons": [],
    }
    rows = [{**row, "id": "mdl-02", "completed": False}]
    [result] = gate.evaluate("t", rows, {"job_completion": {"every": True}})
    assert not result.ok


def test_finding_coverage_needs_the_findings_terms() -> None:
    finding = "No data sources or lineage: the inputs cannot be traced to their systems."
    assert review_eval.finding_covered(finding, "MR-8: no lineage, data sources not traced.")
    assert not review_eval.finding_covered(finding, "MR-8 is missing.")


def test_the_committed_attack_results_are_current_and_meet_their_expectations() -> None:
    committed = json.loads((review_attacks.SET / "results.json").read_text())
    rows = asyncio.run(review_attacks.run_all(review_attacks.load_cases()))
    assert committed["cases"] == json.loads(json.dumps(rows))
    for r in rows:
        assert r["off"]["succeeded"], r["id"]  # every case is a real attack
        assert r["on"]["succeeded"] == (r["expect"] == "none"), r["id"]
        if r["expect"] != "none":
            assert r["expect"] in r["on"]["caught_by"], r["id"]


def attack_gate(guardrails_file: Path) -> gate.GateResult:
    rows = asyncio.run(gate.model_review_attacks(guardrails_file))
    [result] = gate.evaluate("t", rows, gate.load_gates()["model_review_attacks"])
    return result


def test_switching_off_a_guardrail_fails_app_2s_attack_gate(tmp_path: Path) -> None:
    assert attack_gate(SETTINGS.guardrails_file).ok
    raw = yaml.safe_load(SETTINGS.guardrails_file.read_text())
    raw["controls"]["decision_language"]["enabled"] = False
    path = tmp_path / "guardrails.yaml"
    path.write_text(yaml.safe_dump(raw))
    result = attack_gate(path)
    assert not result.ok and "out-stated-validated" in result.detail


@pytest.mark.parametrize(("package_id", "found"), [("mdl-01", True), ("mdl-02", False)])
def test_demo_mode_finds_an_apps_recorded_assessor(
    tmp_path: Path, package_id: str, found: bool
) -> None:
    from dataclasses import replace

    (tmp_path / "model_review" / "assessor" / "v1").mkdir(parents=True)
    (tmp_path / "model_review" / "assessor" / "v1" / "mdl-01.json").write_text("{}")
    settings = replace(SETTINGS, recordings_override=tmp_path)
    assert APP.has_recordings(settings, package_id) is found


def test_the_accepted_live_run_meets_the_live_thresholds() -> None:
    """ADR 0017: the step 3b live run passes the gates set from it, and a run scored like
    the lenient stub model would not."""
    report = json.loads((review_eval.RESULTS / "golden-live.json").read_text())
    rows = [
        {**r, "id": r["package_id"], "completed": r["outcome"] == "drafted"} for r in report["rows"]
    ]
    rules = gate.load_gates()["model_review_golden_live"]
    assert all(r.ok for r in gate.evaluate("live", rows, rules))
    lenient_rows = [
        {**r, "weak_recall": 0.0 if r["weak_recall"] is not None else None} for r in rows
    ]
    assert not all(r.ok for r in gate.evaluate("live", lenient_rows, rules))
