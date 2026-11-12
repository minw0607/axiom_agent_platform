"""Guardrail framework (ADR 0004): switches, fail-closed results, evidence and API."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from axiom.api.app import create_app
from axiom.catalog import load_package
from axiom.evidence.log import verify_chain
from axiom.graph.workflow import run_workflow
from axiom.guardrails import (
    GuardrailConfigError,
    GuardrailResult,
    GuardrailSuite,
    Subject,
    Verdict,
    default_controls,
    latest,
    load_suite,
)
from axiom.guardrails.base import SWITCHED_OFF, Stage
from axiom.jobs.store import InMemoryJobStore
from axiom.jobs.worker import build_context, run_events, run_once
from axiom.memory.checkpoint import allowed_types
from conftest import SETTINGS, demo_context
from test_api import KEYS, SETTINGS_WITH_KEYS, submit
from test_review import COMMENTS, SETTINGS_SOD, review

CONFIG = SETTINGS.guardrails_file
CLOUD_ONLY = {"injection_shield", "content_safety"}


@dataclass
class Fake:
    """A control with a fixed answer that counts how often it runs."""

    name: str
    stage: Stage
    answer: str = "triggered"
    description: str = "test control"
    calls: int = 0

    async def check(self, subject: Subject) -> Verdict:
        self.calls += 1
        if self.answer == "raise":
            raise RuntimeError("service down")
        return Verdict(self.answer, f"{self.name} said {self.answer}", {"hits": 1})  # type: ignore[arg-type]


def subject(node: str = "drafter") -> Subject:
    return Subject("job-1", node)


# The switches ----------------------------------------------------------------------------


def test_the_config_switches_every_control_the_platform_has() -> None:
    suite = load_suite(CONFIG)
    assert set(suite.enabled) == {c.name for c in default_controls()}
    assert all(suite.enabled.values())


def write(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "guardrails.yaml"
    path.write_text(body)
    return path


def test_an_unknown_control_in_the_config_is_refused(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        "controls:\n  injection_shield: {enabled: true}\n  content_safety: {enabled: true}\n"
        "  typo_control: {enabled: false}\n",
    )
    with pytest.raises(GuardrailConfigError, match="unknown controls in config: typo_control"):
        load_suite(path)


def test_a_control_missing_from_the_config_is_refused(tmp_path: Path) -> None:
    path = write(tmp_path, "controls:\n  injection_shield: {enabled: true}\n")
    with pytest.raises(GuardrailConfigError, match="missing from config: content_safety"):
        load_suite(path)


@pytest.mark.parametrize("value", ["'yes'", "1", "null"])
def test_a_switch_must_be_true_or_false(tmp_path: Path, value: str) -> None:
    path = write(
        tmp_path,
        f"controls:\n  injection_shield: {{enabled: {value}}}\n"
        "  content_safety: {enabled: true}\n",
    )
    with pytest.raises(GuardrailConfigError, match="must be true or false"):
        load_suite(path)


# Results never claim a pass they did not earn ----------------------------------------------


def test_a_control_switched_off_is_not_run_and_reports_not_evaluated() -> None:
    control = Fake("rail", "output")
    suite = GuardrailSuite([control], {"rail": False})
    [result] = asyncio.run(suite.run("output", subject()))
    assert control.calls == 0
    assert (result.outcome, result.enabled, result.reason) == ("not_evaluated", False, SWITCHED_OFF)


def test_a_control_that_raises_reports_not_evaluated_not_passed() -> None:
    suite = GuardrailSuite([Fake("rail", "output", "raise")], {"rail": True})
    [result] = asyncio.run(suite.run("output", subject()))
    assert result.outcome == "not_evaluated"
    assert result.reason == "check failed (RuntimeError)"


def test_a_control_switched_on_reports_its_answer() -> None:
    suite = GuardrailSuite([Fake("rail", "output")], {"rail": True})
    [result] = asyncio.run(suite.run("output", subject()))
    assert result == GuardrailResult(
        control="rail",
        stage="output",
        node="drafter",
        outcome="triggered",
        enabled=True,
        reason="rail said triggered",
        details={"hits": 1},
    )


def test_each_stage_runs_only_its_own_controls() -> None:
    a, b = Fake("a", "input", "passed"), Fake("b", "output", "passed")
    suite = GuardrailSuite([a, b], {"a": True, "b": True})
    assert [r.control for r in asyncio.run(suite.run("input", subject("intake")))] == ["a"]
    assert (a.calls, b.calls) == (1, 0)


def test_cloud_only_controls_report_not_evaluated_in_demo_mode() -> None:
    state = asyncio.run(
        run_workflow(demo_context(), "job-demo", load_package(SETTINGS.packages_dir, "pkg-01"))
    )
    results = state["guardrails"]
    assert {r.control for r in results} == CLOUD_ONLY
    assert {r.outcome for r in results} == {"not_evaluated"}
    assert all(r.enabled and "not connected" in r.reason for r in results)
    assert {(r.control, r.stage, r.node) for r in results} == {
        ("injection_shield", "input", "intake"),
        ("content_safety", "output", "drafter"),
    }


def test_latest_keeps_each_controls_last_result_in_first_run_order() -> None:
    def r(control: str, outcome: str) -> GuardrailResult:
        return GuardrailResult(
            control=control,
            stage="output",
            node="drafter",
            outcome=outcome,  # type: ignore[arg-type]
            enabled=True,
            reason="",
        )

    results = [r("a", "triggered"), r("b", "passed"), r("a", "passed")]
    assert [(g.control, g.outcome) for g in latest(results)] == [("a", "passed"), ("b", "passed")]


def test_checkpoints_may_hold_guardrail_results() -> None:
    assert ("axiom.guardrails.base", "GuardrailResult") in allowed_types()


# Evidence log ------------------------------------------------------------------------------


def result_with(guardrails: list[dict[str, object]]) -> dict[str, object]:
    return {
        "completed": ["intake"],
        "model_calls": [{"node": "intake", "attempt": 1}],
        "guardrails": guardrails,
        "source_hashes": {},
    }


def g(control: str, stage: str, node: str = "intake") -> dict[str, object]:
    return {"control": control, "stage": stage, "node": node, "outcome": "triggered"}


def test_input_guardrails_are_logged_before_the_nodes_model_calls() -> None:
    events = run_events(result_with([g("pii", "input"), g("rail", "output")]))
    assert [(e.type, e.payload.get("control")) for e in events] == [
        ("guardrail_checked", "pii"),
        ("model_call", None),
        ("guardrail_checked", "rail"),
        ("node_completed", None),
    ]


def test_a_resumed_segment_logs_only_its_new_guardrail_results() -> None:
    result = result_with([g("pii", "input"), g("rail", "output")])
    events = run_events(result, calls_before=1, completed_before=0, guardrails_before=1)
    assert [e.payload.get("control") for e in events if e.type == "guardrail_checked"] == ["rail"]


@pytest.fixture
def store() -> InMemoryJobStore:
    return InMemoryJobStore()


def work(store: InMemoryJobStore, settings=SETTINGS_WITH_KEYS) -> None:  # type: ignore[no-untyped-def]
    ctx = build_context(settings)
    while asyncio.run(run_once(store, ctx, settings)):
        pass


def test_every_guardrail_result_reaches_the_evidence_log_and_bundle(
    store: InMemoryJobStore,
) -> None:
    with TestClient(create_app(SETTINGS_WITH_KEYS, store)) as client:
        job_id = submit(client).json()["job_id"]
        work(store)
        events = asyncio.run(store.events(job_id))
        checked = [e.payload for e in events if e.type == "guardrail_checked"]
        assert [(p["control"], p["outcome"]) for p in checked] == [
            ("injection_shield", "not_evaluated"),
            ("content_safety", "not_evaluated"),
        ]
        # The input check is logged before intake's first model call.
        types = [e.type for e in events]
        assert types.index("guardrail_checked") < types.index("model_call")
        assert verify_chain(events).verified
        bundle = client.get(
            f"/v1/memos/{job_id}/evidence", headers={"X-API-Key": KEYS["auditor"]}
        ).json()
        assert [b["control"] for b in bundle["guardrails"]] == [
            "injection_shield",
            "content_safety",
        ]


def test_the_memo_shows_each_checks_latest_result_without_internal_names(
    store: InMemoryJobStore,
) -> None:
    with TestClient(create_app(SETTINGS_SOD, store)) as client:
        job_id = submit(client).json()["job_id"]
        work(store, SETTINGS_SOD)
        assert review(client, job_id, "revise", COMMENTS[0]).status_code == 202
        work(store, SETTINGS_SOD)
        body = client.get(f"/v1/memos/{job_id}", headers={"X-API-Key": KEYS["submitter"]}).json()
        shown = body["memo"]["guardrails"]
        # The redraft ran content_safety twice; the memo shows the latest once.
        assert [s["control"] for s in shown] == ["injection_shield", "content_safety"]
        assert all(set(s) == {"control", "stage", "outcome", "reason"} for s in shown)
        events = asyncio.run(store.events(job_id))
        assert sum(e.type == "guardrail_checked" for e in events) == 3


def test_switching_a_control_off_shows_in_the_evidence(
    store: InMemoryJobStore, tmp_path: Path
) -> None:
    config = tmp_path / "config"
    config.mkdir()
    for f in SETTINGS.config_dir.iterdir():
        if f.is_file():
            (config / f.name).write_text(f.read_text())
    (config / "guardrails.yaml").write_text(
        "controls:\n  injection_shield: {enabled: false}\n  content_safety: {enabled: true}\n"
    )
    settings = replace(SETTINGS_WITH_KEYS, config_dir=config)
    with TestClient(create_app(settings, store)) as client:
        job_id = submit(client).json()["job_id"]
        work(store, settings)
        checked = [
            e.payload for e in asyncio.run(store.events(job_id)) if e.type == "guardrail_checked"
        ]
        assert checked[0]["control"] == "injection_shield"
        assert (checked[0]["enabled"], checked[0]["reason"]) == (False, SWITCHED_OFF)
