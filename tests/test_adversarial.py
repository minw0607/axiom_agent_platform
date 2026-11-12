"""Adversarial set v1: each case meets its expectation, and the committed results are current."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "evals" / "adversarial"))
import harness

CASES = harness.load_cases()
CLOUD_ONLY = {"injection_shield", "content_safety"}


@pytest.fixture(scope="module")
def rows() -> list[dict[str, Any]]:
    return asyncio.run(harness.run_all(CASES))


def test_every_attack_gets_through_with_the_guardrails_off(rows: list[dict[str, Any]]) -> None:
    assert [r["id"] for r in rows if not r["off"]["succeeded"]] == []


def test_each_case_is_stopped_by_the_control_meant_for_it(rows: list[dict[str, Any]]) -> None:
    for r in rows:
        if r["expect"] in ("none", *CLOUD_ONLY):
            continue
        assert not r["on"]["succeeded"], r["id"]
        assert r["expect"] in r["on"]["caught_by"], r["id"]


def test_known_misses_still_get_through(rows: list[dict[str, Any]]) -> None:
    """If a change starts catching one, update its `expect` in cases.yaml and the results."""
    for r in rows:
        if r["expect"] == "none" or r["expect"] in CLOUD_ONLY:
            assert r["on"]["succeeded"], r["id"]


def test_the_committed_results_match_a_fresh_run(rows: list[dict[str, Any]]) -> None:
    committed = json.loads((harness.SET / "results.json").read_text())
    assert committed == {"summary": harness.summary(rows), "cases": rows}, (
        "run: uv run python evals/adversarial/harness.py"
    )
