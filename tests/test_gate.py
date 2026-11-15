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
