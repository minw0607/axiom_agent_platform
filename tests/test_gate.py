"""The release gate runner: its rules, its config, and week 8's acceptance test B (a guardrail
switched off in config fails the red-team gate)."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "evals"))
import gate

from conftest import SETTINGS


def row(id_: str, completed: bool = True, **metrics: Any) -> dict[str, Any]:
    return {"id": id_, "completed": completed, **metrics}


def test_an_every_rule_names_the_runs_that_miss() -> None:
    rows = [row("a", m=1.0), row("b", m=0.5), row("c", m=1.0)]
    [result] = gate.evaluate("s", rows, {"m": {"every": 1.0}})
    assert not result.ok and "2/3 runs" in result.detail and "b (0.5)" in result.detail


def test_a_value_that_was_not_evaluated_never_passes_an_every_rule() -> None:
    [result] = gate.evaluate("s", [row("a", m=None)], {"m": {"every": 1.0}})
    assert not result.ok


def test_a_mean_rule_skips_values_that_were_not_evaluated() -> None:
    rows = [row("a", m=1.0), row("b", m=0.8), row("c", m=None)]
    assert gate.evaluate("s", rows, {"m": {"mean": 0.9}})[0].ok
    assert not gate.evaluate("s", rows, {"m": {"mean": 0.95}})[0].ok


def test_a_rate_rule_counts_true_values() -> None:
    rows = [row("a", m=True), row("b", m=False), row("c", m=True), row("d", m=True)]
    assert gate.evaluate("s", rows, {"m": {"rate": 0.75}})[0].ok
    assert not gate.evaluate("s", rows, {"m": {"rate": 0.8}})[0].ok


def test_exceptions_are_excused_and_named() -> None:
    rows = [row("pkg-1", ok=True), row("pkg-2", ok=False)]
    [result] = gate.evaluate("s", rows, {"ok": {"every": True, "exceptions": {"pkg-2": "gap"}}})
    assert result.ok and "excused: pkg-2" in result.detail


def test_metrics_are_judged_on_completed_runs_but_job_completion_on_all() -> None:
    rows = [row("a", m=1.0, job_completion=True), row("b", False, m=0.0, job_completion=False)]
    metric, completion = gate.evaluate(
        "s", rows, {"m": {"every": 1.0}, "job_completion": {"every": True}}
    )
    assert metric.ok and not completion.ok


def test_a_metric_no_run_reports_is_a_config_error() -> None:
    with pytest.raises(ValueError, match="no run reports 'typo'"):
        gate.evaluate("s", [row("a", m=1.0)], {"typo": {"every": 1.0}})


def test_a_rule_needs_exactly_one_kind(tmp_path: Path) -> None:
    path = tmp_path / "gates.yaml"
    path.write_text(yaml.safe_dump({"suites": {"s": {"m": {"every": 1.0, "mean": 0.9}}}}))
    with pytest.raises(ValueError, match="exactly one"):
        gate.load_gates(path)


def test_every_configured_suite_has_a_runner() -> None:
    gates = gate.load_gates()
    runners = {suite for suite, _ in [*gate.LOCAL.values(), *gate.STACK.values()]}
    runners |= {spec["suite"] for spec in gate.load_live().values()}
    assert set(gates) == runners
    # ADR 0008's two tracked gaps are the only excused packages.
    excused = gates["golden"]["recommendation_acceptable"]["exceptions"]
    assert set(excused) == {"pkg-21", "pkg-23"}


def attack_gate(guardrails_file: Path) -> gate.GateResult:
    rows = asyncio.run(gate.attacks_configured(guardrails_file))
    [result] = gate.evaluate("attacks", rows, gate.load_gates()["attacks"])
    return result


def test_the_red_team_gate_passes_with_the_guardrails_as_committed() -> None:
    assert attack_gate(SETTINGS.guardrails_file).ok


def test_switching_off_a_guardrail_fails_the_red_team_gate(tmp_path: Path) -> None:
    raw = yaml.safe_load(SETTINGS.guardrails_file.read_text())
    raw["controls"]["injection_screen"]["enabled"] = False
    path = tmp_path / "guardrails.yaml"
    path.write_text(yaml.safe_dump(raw))
    result = attack_gate(path)
    assert not result.ok and "doc-override" in result.detail


def test_a_max_drop_rule_also_compares_with_main() -> None:
    rules = {"m": {"mean": 0.8, "max_drop": 0.05}}
    main = [row("a", m=1.0), row("b", m=1.0), row("z", m=0.0)]  # z is not in this run
    assert gate.evaluate("s", [row("a", m=1.0), row("b", m=0.9)], rules, main)[0].ok
    [result] = gate.evaluate("s", [row("a", m=1.0), row("b", m=0.8)], rules, main)
    assert not result.ok and "main 1.000" in result.detail


def test_max_drop_needs_a_baseline_and_a_mean_rule(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="needs a baseline"):
        gate.evaluate("s", [row("a", m=1.0)], {"m": {"mean": 0.8, "max_drop": 0.05}})
    path = tmp_path / "gates.yaml"
    path.write_text(yaml.safe_dump({"suites": {"s": {"m": {"every": 1.0, "max_drop": 0.1}}}}))
    with pytest.raises(ValueError, match="max_drop applies to a mean"):
        gate.load_gates(path)


def test_repeats_are_scored_per_package() -> None:
    rules = {
        "job_completion": {"every": True},
        "recommendation_acceptable": {"every": True},
        "exact": {"every": 1.0},
        "coverage": {"mean": 0.9},
    }
    runs = [
        row("p", job_completion=True, recommendation_acceptable=True, exact=1.0, coverage=1.0),
        row("p", job_completion=True, recommendation_acceptable=False, exact=0.5, coverage=0.8),
        row("p", job_completion=True, recommendation_acceptable=True, exact=1.0, coverage=0.9),
        row("q", False, job_completion=False, recommendation_acceptable=None),
    ]
    p, q = gate.per_package(runs, rules)
    assert p["recommendation_acceptable"] is True  # two of three
    assert p["exact"] == 0.5 and p["coverage"] == pytest.approx(0.9)  # worst; average
    assert not q["completed"] and not q["job_completion"]


def test_the_smoke_set_is_the_one_adr_0008_names() -> None:
    live = gate.load_live()
    assert live["smoke"]["packages"] == ["pkg-04", "pkg-06", "pkg-08", "pkg-11", "pkg-21"]
    assert live["full"] == {**live["full"], "packages": "all", "repeats": 3}


def test_the_degraded_run_swaps_only_the_drafter_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "MODEL_ALIAS_MAP",
        "AXIOM_MODE",
        "AXIOM_RECORD",
        "AXIOM_PROMPTS_DIR",
        "GKN_MCP_URL",
        "REGULUS_MCP_URL",
        "TOOL_GATEWAY_URL",
        "MCP_SHARED_SECRET",
    ):
        monkeypatch.setenv(name, "set-by-test")
    monkeypatch.setattr(gate, "_load_env_files", lambda *paths: None)
    gate._live_env(degrade=True)
    import os

    from axiom.apps.credit_memo.agents.base import PROMPT_VERSIONS

    prompts = Path(os.environ["AXIOM_PROMPTS_DIR"])
    drafter = prompts / "credit_memo" / "drafter" / f"{PROMPT_VERSIONS['drafter']}.md"
    assert drafter.read_text() == gate.DEGRADED_DRAFTER.read_text()
    real = SETTINGS.app_prompts_dir("credit_memo")
    assert (prompts / "credit_memo" / "analyst" / "v1.md").read_text() == (
        real / "analyst" / "v1.md"
    ).read_text()
    assert os.environ["AXIOM_MODE"] == "local" and os.environ["AXIOM_RECORD"] == ""
    assert os.environ["GKN_MCP_URL"] == ""  # in-memory retrieval, like the baseline


def test_a_failed_job_shows_its_reason() -> None:
    rows = [
        gate._golden_row({"package_id": "pkg-01", "outcome": "drafted"}),
        gate._golden_row(
            {
                "package_id": "pkg-02",
                "outcome": "guardrail_failed",
                "guardrails_triggered": ["number_provenance@drafter"],
            }
        ),
    ]
    [result] = gate.evaluate("s", rows, {"job_completion": {"every": True}})
    assert "pkg-02 (guardrail_failed: number_provenance@drafter)" in result.detail
