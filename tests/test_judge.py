"""The evaluation grader: answers, independence, agreement measures, the calibration set."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "evals"), str(ROOT / "evals" / "judge")]
import baseline  # noqa: E402
import build_calibration  # noqa: E402
import calibrate  # noqa: E402
import grader  # noqa: E402

from axiom.agents.base import AgentContext  # noqa: E402
from axiom.catalog import load_ground_truth, load_package  # noqa: E402
from axiom.graph.workflow import run_workflow  # noqa: E402
from axiom.mcp_clients.evidence import InMemoryEvidence  # noqa: E402
from axiom.router.router import AliasTarget, ModelRouter, RoutingPolicy  # noqa: E402
from axiom.router.types import ModelAdapter, ModelRequest, ModelResponse  # noqa: E402
from axiom.tools.policy import load_policy  # noqa: E402
from conftest import SETTINGS, demo_context  # noqa: E402

ITEM = grader.CitationItem(
    "t1",
    "FY2025 revenue was 13801200.",
    (grader.Source("d", "line 8", "| Revenue | 13,801,200 |"),),
)


def judge_context(
    answer: Any, judge_alias: str = "grader"
) -> tuple[AgentContext, list[ModelRequest]]:
    """A context whose judge route answers with `answer(request)` under its own alias."""
    seen: list[ModelRequest] = []

    class Fake:
        provider = "fake"

        async def complete(self, request: ModelRequest, deployment: str) -> ModelResponse:
            seen.append(request)
            return ModelResponse(
                text=answer(request), input_tokens=1, output_tokens=1, latency_ms=0, origin="live"
            )

    def factory(provider: str) -> ModelAdapter:
        return Fake()

    router = ModelRouter(
        RoutingPolicy(routes={"judge": {"primary": judge_alias}}),
        {judge_alias: AliasTarget("fake", "-")},
        SETTINGS.recordings_dir,
        adapter_factory=factory,
    )
    return AgentContext(
        router, InMemoryEvidence(SETTINGS.reference_dirs), load_policy(), SETTINGS.prompts_dir
    ), seen


def verdict(v: str) -> Any:
    """Answer every question yes (or no): `supported` for citations, `covered` for findings."""
    yes = v in ("supported", "covered")

    def answer(request: ModelRequest) -> str:
        word = "supported" if request.prompt_id == "judge_citation" else "covered"
        return json.dumps({"verdict": word if yes else f"not_{word}", "reason": "test"})

    return answer


# Answers ---------------------------------------------------------------------------------


def test_a_valid_answer_becomes_a_grade_with_the_graders_alias() -> None:
    ctx, seen = judge_context(verdict("supported"))
    g = asyncio.run(grader.grade_citation(ctx, ITEM))
    assert (g.verdict, g.alias) == ("supported", "grader")
    sent = json.loads(seen[0].messages[-1].content)
    assert sent["claim"] == ITEM.claim and sent["context"] == grader.CONTEXT["neutral"]
    assert seen[0].route == "judge" and seen[0].recording_key == "judge_citation/v1/t1.neutral"


def test_the_high_stakes_note_is_the_only_difference_between_framings() -> None:
    ctx, seen = judge_context(verdict("supported"))
    asyncio.run(grader.grade_citation(ctx, ITEM, "neutral"))
    asyncio.run(grader.grade_citation(ctx, ITEM, "high"))
    a, b = (json.loads(r.messages[-1].content) for r in seen)
    assert a.pop("context") != b.pop("context") and a == b


def test_an_invalid_answer_twice_is_not_evaluated_never_a_verdict() -> None:
    ctx, _ = judge_context(lambda request: "I think it is fine")
    g = asyncio.run(grader.grade_finding(ctx, grader.FindingItem("f1", "DSCR 1.24", ("Memo.",))))
    assert g.verdict is None and "AgentOutputError" in g.reason


def test_without_a_judge_route_recording_the_grade_is_not_evaluated() -> None:
    g = asyncio.run(grader.grade_citation(demo_context(), ITEM))
    assert g.verdict is None and "ProviderError" in g.reason


def test_a_model_never_grades_its_own_memo() -> None:
    g = grader.Grade("t1", "neutral", "supported", "ok", alias="frontier-a")
    assert grader.independent(g, {"frontier-a"}).verdict is None
    assert grader.independent(g, {"small-a"}).verdict == "supported"


def test_the_local_judge_route_differs_from_the_drafter_and_has_no_fallback() -> None:
    routes = yaml.safe_load((SETTINGS.config_dir / "routing.local.yaml").read_text())["routes"]
    assert routes["judge"] == {"primary": routes["narrative"]["fallback"]}
    assert routes["judge"]["primary"] != routes["narrative"]["primary"]


# Grading a memo in the runner ------------------------------------------------------------


def drafted(package_id: str = "pkg-01") -> Any:
    package = load_package(SETTINGS.packages_dir, package_id)
    return asyncio.run(run_workflow(demo_context(), f"j-{package_id}", package))


def test_the_runner_grades_every_cited_claim_and_every_key_finding() -> None:
    state, truth = drafted(), load_ground_truth(SETTINGS.packages_dir, "pkg-01")
    ctx, seen = judge_context(verdict("supported"))
    out = asyncio.run(baseline.judge_memo(ctx, state, truth, "t"))
    cited = sum(bool(c.citations) for s in state["draft"].sections for c in s.claims)
    assert sum(r.prompt_id == "judge_citation" for r in seen) == cited
    assert sum(r.prompt_id == "judge_finding" for r in seen) == len(truth["golden"]["key_findings"])
    assert out["citation_support_judge"] == 1.0 and out["key_finding_recall_judge"] == 1.0
    # Every source handed to the grader is the cited line's own text.
    first = json.loads(
        next(r for r in seen if r.prompt_id == "judge_citation").messages[-1].content
    )
    assert all(s["text"] for s in first["sources"])


def test_the_runner_reports_none_when_the_grader_drafted_the_memo() -> None:
    state, truth = drafted(), load_ground_truth(SETTINGS.packages_dir, "pkg-01")
    drafter_alias = next(c.alias for c in state["model_calls"] if c.node == "drafter")
    ctx, _ = judge_context(verdict("supported"), judge_alias=drafter_alias)
    out = asyncio.run(baseline.judge_memo(ctx, state, truth, "t"))
    assert out["citation_support_judge"] is None and out["key_finding_recall_judge"] is None
    assert out["judge_not_evaluated"] > 0


# Agreement measures ----------------------------------------------------------------------


def test_kappa() -> None:
    assert calibrate.kappa([(True, True), (False, False)] * 5) == 1.0
    assert calibrate.kappa([(True, True), (True, False), (False, True), (False, False)]) == 0.0
    assert calibrate.kappa([]) is None


def test_analysis_counts_stakes_flips_agreement_and_altered_items() -> None:
    items = calibrate.load_items()[:4]
    grades = {}
    for n, i in enumerate(items):
        yes = "supported" if i["type"] == "citation" else "covered"
        no = "not_" + yes
        neutral = no if i["built_as"] != "as_drafted" else yes
        high = no if n == 0 else neutral  # the first item flips under high stakes
        grades[i["item_id"]] = {
            "neutral": grader.Grade(i["item_id"], "neutral", neutral),
            "high": grader.Grade(i["item_id"], "high", high),
        }
    labels = {
        items[0]["item_id"]: {"verdict": "supported"},
        items[1]["item_id"]: {"verdict": "unsure"},
    }
    s = calibrate.analyse(items, grades, labels)["summary"]
    assert s["stakes_flip_rate"] == 0.25 and s["labelled"] == 2 and s["labelled_not_sure"] == 1
    assert s["compared_with_labels"] == 1 and s["catches_altered_items"] == 1.0


# The calibration set ---------------------------------------------------------------------


def test_the_calibration_set_has_both_answers_and_rebuilds_identically(tmp_path: Path) -> None:
    items = calibrate.load_items()
    built = [i["built_as"] for i in items]
    assert len(items) == 30 and sum(i["type"] == "citation" for i in items) == 20
    assert sum(b in calibrate.ALTERED for b in built) == 7
    assert sum(b.startswith("finding_from_") for b in built) == 4
    old = build_calibration.OUT
    build_calibration.OUT = tmp_path / "calibration.json"
    try:
        build_calibration.main()
        assert build_calibration.OUT.read_text() == old.read_text()
    finally:
        build_calibration.OUT = old


@pytest.mark.parametrize("item", calibrate.load_items()[:20], ids=lambda i: i["item_id"])
def test_every_citation_item_shows_the_cited_lines_text(item: dict[str, Any]) -> None:
    assert item["sources"] and all(s["text"].strip() for s in item["sources"])


def test_the_labelling_page_never_shows_how_an_item_was_built() -> None:
    sys.path.insert(0, str(ROOT / "evals" / "judge" / "label_page"))
    import build as label_page

    html = label_page.render()
    assert "built_as" not in html
    for kind in (
        "as_drafted",
        "changed_number",
        "wrong_line",
        "unsupported_clause",
        "finding_from_",
    ):
        assert kind not in html
    assert all(i["item_id"] in html for i in calibrate.load_items())
