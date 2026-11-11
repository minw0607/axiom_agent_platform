"""Compliance reviewer: lending-reference findings, output validation, and failing closed."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace

import pytest
import yaml

from axiom.agents.base import AgentContext
from axiom.agents.compliance import (
    TOPICS,
    ComplianceReview,
    TopicAssessment,
    topics_for,
    validate_review,
)
from axiom.catalog import list_packages
from axiom.domain import MemoDraft, Severity
from axiom.mcp_clients.evidence import InMemoryEvidence
from axiom.router.types import ModelRequest
from conftest import SETTINGS, fake_context
from test_workflow import run

PACKAGE_IDS = [p["package_id"] for p in list_packages(SETTINGS.packages_dir)]
REFS = SETTINGS.data_dir / "lending_refs"


def reviewer(state: dict) -> dict[str, object]:  # type: ignore[type-arg]
    return {f.rule_id: f for f in state["findings"] if f.source == "compliance_reviewer"}


@pytest.mark.parametrize("package_id", PACKAGE_IDS)
def test_every_draft_gets_cited_reference_findings(ctx: AgentContext, package_id: str) -> None:
    state = run(ctx, package_id)
    found = reviewer(state)
    expected = {t.rule_id for t in topics_for(state["draft"])}
    assert set(found) == expected
    for f in found.values():
        assert f.outcome == "passed"  # type: ignore[attr-defined]
        assert f.citations  # type: ignore[attr-defined]
        assert {c.doc_id for c in f.citations} <= {"regulation_b", "occ_rating_credit_risk"}  # type: ignore[attr-defined]
    assert state["reference_evidence"]
    assert {h.collection for h in state["reference_evidence"]} == {"lending_refs"}


def test_adverse_action_reasons_apply_only_to_a_draft_decline(ctx: AgentContext) -> None:
    decline = run(ctx, "pkg-04")
    assert decline["draft"].draft_recommendation == "decline"
    assert "REF_ADVERSE_ACTION_REASONS" in reviewer(decline)
    assert "REF_ADVERSE_ACTION_REASONS" not in reviewer(run(ctx, "pkg-01"))


# Output validation -------------------------------------------------------------------


def _assessment(rule_id: str, **changes: object) -> TopicAssessment:
    base = {
        "rule_id": rule_id,
        "outcome": "passed",
        "message": "ok",
        "claim_ids": ["s1.c1"],
        "reference_chunk_ids": ["regulation_b#5"],
    }
    return TopicAssessment.model_validate({**base, **changes})


def _errors(*assessments: TopicAssessment) -> list[str]:
    return validate_review(
        ComplianceReview(assessments=list(assessments)),
        TOPICS[:2],
        claim_ids={"s1.c1"},
        chunk_ids={"regulation_b#5"},
    )


def test_a_valid_review_passes_validation() -> None:
    assert _errors(_assessment("REF_PROHIBITED_BASIS"), _assessment("REF_REPAYMENT_CAPACITY")) == []


@pytest.mark.parametrize(
    ("assessments", "needle"),
    [
        ([_assessment("REF_PROHIBITED_BASIS")], "assess exactly these topics"),
        (
            [
                _assessment("REF_PROHIBITED_BASIS"),
                _assessment("REF_REPAYMENT_CAPACITY", reference_chunk_ids=["made_up#1"]),
            ],
            "not a retrieved reference passage",
        ),
        (
            [
                _assessment("REF_PROHIBITED_BASIS", reference_chunk_ids=[]),
                _assessment("REF_REPAYMENT_CAPACITY"),
            ],
            "cite at least one reference passage",
        ),
        (
            [
                _assessment("REF_PROHIBITED_BASIS", claim_ids=["s9.c9"]),
                _assessment("REF_REPAYMENT_CAPACITY"),
            ],
            "not a claim id",
        ),
        (
            [
                _assessment("REF_PROHIBITED_BASIS", outcome="triggered", claim_ids=[]),
                _assessment("REF_REPAYMENT_CAPACITY"),
            ],
            "name the claim ids",
        ),
    ],
)
def test_invalid_reviews_are_rejected(assessments: list[TopicAssessment], needle: str) -> None:
    assert any(needle in e for e in _errors(*assessments))


# Judgments and failing closed --------------------------------------------------------


def _review_responder(outcome: str) -> object:
    def respond(request: ModelRequest) -> str:
        if request.prompt_id != "compliance":
            return ""
        payload = json.loads(request.messages[1].content)
        chunk = payload["references"][0]["chunk_id"]
        first_claim = payload["claims"][0]["claim_id"]
        return json.dumps(
            {
                "assessments": [
                    {
                        "rule_id": t["rule_id"],
                        "outcome": outcome,
                        "message": "concern noted" if outcome == "triggered" else "ok",
                        "claim_ids": [first_claim],
                        "reference_chunk_ids": [chunk],
                    }
                    for t in payload["topics"]
                ]
            }
        )

    return respond


def test_a_triggered_topic_takes_its_severity_from_code() -> None:
    ctx, model = fake_context(_review_responder("triggered"))  # type: ignore[arg-type]
    state = run(ctx, "pkg-01")
    found = reviewer(state)
    assert found["REF_PROHIBITED_BASIS"].severity == Severity.HIGH  # type: ignore[attr-defined]
    assert found["REF_REPAYMENT_CAPACITY"].severity == Severity.WARNING  # type: ignore[attr-defined]
    assert all(f.citations for f in found.values())  # type: ignore[attr-defined]
    assert [r.prompt_id for r in model.requests] == ["compliance"]


def test_an_invalid_review_is_not_evaluated_and_the_job_continues() -> None:
    ctx, _ = fake_context(lambda r: '{"assessments": []}' if r.prompt_id == "compliance" else "")
    state = run(ctx, "pkg-01")
    assert "error" not in state and state["completed"][-1] == "compliance"
    found = reviewer(state)
    assert {f.outcome for f in found.values()} == {"not_evaluated"}  # type: ignore[attr-defined]
    rejected = [c for c in state["model_calls"] if c.node == "compliance"]
    assert [(c.attempt, c.valid) for c in rejected] == [(1, False), (2, False)]


def test_no_reference_passages_means_not_evaluated(ctx: AgentContext) -> None:
    bare = replace(ctx, evidence=InMemoryEvidence())
    state = run(bare, "pkg-01")
    assert "error" not in state
    found = reviewer(state)
    assert set(found) == {t.rule_id for t in topics_for(state["draft"])}
    assert {f.outcome for f in found.values()} == {"not_evaluated"}  # type: ignore[attr-defined]
    assert not [c for c in state["model_calls"] if c.node == "compliance"]


def test_topics_for_a_draft_decline_include_adverse_action() -> None:
    draft = MemoDraft(sections=(), draft_recommendation="decline")
    assert [t.rule_id for t in topics_for(draft)] == [t.rule_id for t in TOPICS]


# The reference set and its provenance --------------------------------------------------


def test_manifest_covers_every_reference_file_and_matches_its_hash() -> None:
    manifest = yaml.safe_load((REFS / "manifest.yaml").read_text())
    files = {p.name for p in REFS.glob("*.md") if p.stem.lower() != "readme"}
    entries = {r["file"]: r for r in manifest["references"]}
    assert set(entries) == files
    for name, entry in entries.items():
        digest = hashlib.sha256((REFS / name).read_bytes()).hexdigest()
        assert entry["sha256"] == digest, f"{name} changed; update manifest.yaml"
        assert entry["status"] == "current"
        assert entry["source_url"].startswith("https://")
        assert entry["doc_id"] == name.removesuffix(".md")
        assert entry["retrieved"]


def test_every_reference_passage_names_its_provision() -> None:
    for path in REFS.glob("*.md"):
        if path.stem.lower() == "readme":
            continue
        paras = [p for p in path.read_text().split("\n\n") if p.strip()]
        body = [p for p in paras if not p.startswith(("#", "Source:", "These", "The booklet"))]
        assert body and all(p.startswith("[") for p in body), path.name
