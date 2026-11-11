"""Workflow and agents, with recorded or fake models (no network)."""

from __future__ import annotations

import asyncio
import json
from decimal import Decimal

import pytest

from axiom.agents.base import AgentContext
from axiom.agents.drafter import validate_draft
from axiom.agents.intake import DOC_FIELDS, ExtractedFigure, check_extraction, verify_figure
from axiom.catalog import list_packages, load_ground_truth, load_package
from axiom.domain import Citation, Claim, MemoDraft, MemoSection
from axiom.graph.workflow import run_workflow
from axiom.router.types import ModelRequest
from conftest import SETTINGS, fake_context

PACKAGE_IDS = [p["package_id"] for p in list_packages(SETTINGS.packages_dir)]
EXPECTED_RECOMMENDATION = {
    "healthy": "approve",
    "moderate": "approve",
    "weak_dscr": "decline",
    "leverage_breach": "approve_with_conditions",
    "inconsistent_figures": "refer",
    "missing_year": "approve_with_conditions",
    "concentration": "approve_with_conditions",
    "revenue_decline": "approve_with_conditions",
    "low_liquidity": "approve_with_conditions",
}


def run(ctx: AgentContext, package_id: str) -> dict:  # type: ignore[type-arg]
    package = load_package(SETTINGS.packages_dir, package_id)
    return dict(asyncio.run(run_workflow(ctx, f"test-{package_id}", package)))


# End to end in demo mode -------------------------------------------------------


@pytest.mark.parametrize("package_id", PACKAGE_IDS)
def test_demo_run_produces_a_cited_draft(ctx: AgentContext, package_id: str) -> None:
    state = run(ctx, package_id)
    truth = load_ground_truth(SETTINGS.packages_dir, package_id)
    assert "error" not in state
    assert state["completed"] == ["intake", "analyst", "drafter"]
    assert state["draft"].draft_recommendation == EXPECTED_RECOMMENDATION[truth["scenario"]]
    # Every extracted figure matches ground truth, value and source line.
    fs = state["financials"]
    for year, fields in truth["years"].items():
        for name, raw in fields.items():
            fig = fs.years[int(year)][name]
            assert fig.value == Decimal(raw["value"])
            assert (fig.source.doc_id, fig.source.line) == (raw["doc_id"], raw["line"])
    # Recorded responses are labeled as such in the evidence trail.
    assert {c.origin for c in state["model_calls"]} == {"scripted"}


# Intake: every figure must be supported by its source line ----------------------


def _doc(kind: str = "income_statement"):  # type: ignore[no-untyped-def]
    doc = load_package(SETTINGS.packages_dir, "pkg-01").document(kind)  # type: ignore[arg-type]
    assert doc is not None
    return doc


ALLOWED = ("revenue", "ebitda")


def test_figure_on_its_line_and_column_is_accepted() -> None:
    fig = ExtractedFigure(field="revenue", year=2024, value="13144000", line=8)
    assert verify_figure(_doc(), fig, ALLOWED) is None


@pytest.mark.parametrize(
    ("fig", "message"),
    [
        (ExtractedFigure(field="revenue", year=2023, value="13144000", line=8), "shows"),
        (ExtractedFigure(field="revenue", year=2024, value="13144000", line=9), "shows"),
        (ExtractedFigure(field="revenue", year=2019, value="13144000", line=8), "no column"),
        (ExtractedFigure(field="revenue", year=2024, value="13144000", line=99), "not exist"),
        (ExtractedFigure(field="revenue", year=2024, value="about 13m", line=8), "not a number"),
        (ExtractedFigure(field="net_income", year=2024, value="1", line=8), "not requested"),
        (ExtractedFigure(field="revenue", year=None, value="13144000", line=8), "year is req"),
    ],
)
def test_unsupported_figures_are_rejected(fig: ExtractedFigure, message: str) -> None:
    error = verify_figure(_doc(), fig, ALLOWED)
    assert error is not None and message in error


def test_loan_terms_are_checked_on_their_line() -> None:
    doc = _doc("loan_application")
    allowed = ("requested_amount", "interest_rate_pct")
    ok = ExtractedFigure(field="interest_rate_pct", year=None, value="7.25", line=6)
    bad = ExtractedFigure(field="requested_amount", year=None, value="2500000", line=5)
    assert verify_figure(doc, ok, allowed) is None
    assert verify_figure(doc, bad, allowed) is not None


def _truth_figures(package_id: str, kind: str) -> list[ExtractedFigure]:
    """The complete, correct extraction for one statement, from the ground truth."""
    truth = load_ground_truth(SETTINGS.packages_dir, package_id)
    doc_id = f"{package_id}-{kind}"
    return [
        ExtractedFigure(field=name, year=int(year), value=raw["value"], line=raw["line"])
        for year, fields in truth["years"].items()
        for name, raw in fields.items()
        if raw["doc_id"] == doc_id
    ]


@pytest.mark.parametrize("kind", ["income_statement", "balance_sheet", "cash_flow"])
def test_complete_extraction_is_accepted(kind: str) -> None:
    figures = _truth_figures("pkg-01", kind)
    assert figures
    assert check_extraction(_doc(kind), figures, DOC_FIELDS[kind]) == []


def test_latest_year_only_is_rejected() -> None:
    figures = [f for f in _truth_figures("pkg-01", "income_statement") if f.year == 2025]
    errors = check_extraction(_doc(), figures, DOC_FIELDS["income_statement"])
    assert len(errors) == len(figures)  # one per row
    assert "line 8 ('Revenue'): not extracted for FY2023, FY2024" in errors


def test_skipped_row_is_rejected() -> None:
    kind = "cash_flow"
    figures = [f for f in _truth_figures("pkg-01", kind) if f.field != "debt_principal_repaid"]
    errors = check_extraction(_doc(kind), figures, DOC_FIELDS[kind])
    assert len(errors) == 1
    assert "'Repayment of long-term debt'" in errors[0] and "none was extracted" in errors[0]


def test_missing_year_package_needs_only_the_years_it_has() -> None:
    figures = _truth_figures("pkg-07", "income_statement")
    doc = load_package(SETTINGS.packages_dir, "pkg-07").document("income_statement")
    assert doc is not None
    assert check_extraction(doc, figures, DOC_FIELDS["income_statement"]) == []


def test_incomplete_extraction_twice_fails_the_job() -> None:
    def latest_year_only(request: ModelRequest) -> str:
        key = request.recording_key.removesuffix(".retry")
        if request.prompt_id != "intake" or not key.endswith("income_statement"):
            return ""
        figures = [f for f in _truth_figures("pkg-01", "income_statement") if f.year == 2025]
        return json.dumps({"figures": [f.model_dump() for f in figures]})

    ctx, model = fake_context(latest_year_only)
    state = run(ctx, "pkg-01")
    assert state["error"].startswith("AgentOutputError")
    assert state["error_node"] == "intake"
    assert "not extracted for FY2023, FY2024" in model.requests[1].messages[-1].content


def _bad_intake(request: ModelRequest) -> str:
    key = request.recording_key.removesuffix(".retry")
    if request.prompt_id != "intake" or not key.endswith("income_statement"):
        return ""
    return json.dumps(
        {"figures": [{"field": "revenue", "year": 2025, "value": "99999999", "line": 8}]}
    )


def test_invalid_extraction_twice_fails_the_job() -> None:
    ctx, model = fake_context(_bad_intake)
    state = run(ctx, "pkg-01")
    assert state["error"].startswith("AgentOutputError")
    assert state["error_node"] == "intake"
    assert len(model.requests) == 2  # one retry, with the validation errors
    assert "line 8 shows" in model.requests[1].messages[-1].content
    assert state["completed"] == []  # the supervisor stopped the run


def test_invalid_extraction_then_valid_retry_continues() -> None:
    def first_attempt_bad(request: ModelRequest) -> str:
        return "" if request.recording_key.endswith(".retry") else _bad_intake(request)

    ctx, _ = fake_context(first_attempt_bad)
    state = run(ctx, "pkg-01")
    assert "error" not in state
    attempts = [(c.attempt, c.valid) for c in state["model_calls"] if c.node == "intake"]
    assert (1, False) in attempts and (2, True) in attempts


# Drafter: citations, template, and recommendation consistency -------------------


def _state(ctx: AgentContext, package_id: str) -> dict:  # type: ignore[type-arg]
    return run(ctx, package_id)


def test_draft_validation_rejects_uncited_and_unknown_citations(ctx: AgentContext) -> None:
    state = _state(ctx, "pkg-01")
    package = load_package(SETTINGS.packages_dir, "pkg-01")
    draft = MemoDraft(
        sections=(
            MemoSection(heading="Borrower and request", claims=(Claim(text="Uncited fact."),)),
            MemoSection(
                heading="Financial analysis",
                claims=(Claim(text="x", citations=(Citation(doc_id="nope", locator="line 1"),)),),
            ),
            MemoSection(
                heading="Risk factors",
                claims=(
                    Claim(
                        text="y",
                        citations=(Citation(doc_id="pkg-01-income_statement", locator="line 99"),),
                    ),
                ),
            ),
            MemoSection(
                heading="Draft recommendation", claims=(Claim(text="The loan is approved."),)
            ),
        ),
        draft_recommendation="approve",
    )
    errors = validate_draft(draft, package, set(), state["risk_flags"], state["checks"])
    joined = " | ".join(errors)
    assert "uncited claim" in joined
    assert "unknown document" in joined
    assert "missing line" in joined
    assert "final-decision language" in joined


def test_recommendation_must_match_deterministic_findings(ctx: AgentContext) -> None:
    weak = _state(ctx, "pkg-05")  # pro forma leverage above policy
    draft = weak["draft"].model_copy(update={"draft_recommendation": "approve"})
    package = load_package(SETTINGS.packages_dir, "pkg-05")
    errors = validate_draft(draft, package, set(), weak["risk_flags"], weak["checks"])
    assert any("cannot be 'approve'" in e for e in errors)

    broken = _state(ctx, "pkg-06")  # balance sheet does not balance
    draft = broken["draft"].model_copy(update={"draft_recommendation": "approve_with_conditions"})
    package = load_package(SETTINGS.packages_dir, "pkg-06")
    errors = validate_draft(draft, package, set(), broken["risk_flags"], broken["checks"])
    assert any("must be 'refer'" in e for e in errors)


def test_dscr_below_the_floor_must_be_declined(ctx: AgentContext) -> None:
    weak = _state(ctx, "pkg-04")  # pro forma DSCR 0.93
    assert "DSCR_BELOW_DECLINE_FLOOR" in {f.code for f in weak["risk_flags"]}
    assert weak["draft"].draft_recommendation == "decline"
    package = load_package(SETTINGS.packages_dir, "pkg-04")
    draft = weak["draft"].model_copy(update={"draft_recommendation": "approve_with_conditions"})
    errors = validate_draft(draft, package, set(), weak["risk_flags"], weak["checks"])
    assert any("must be 'decline'" in e for e in errors)


@pytest.mark.parametrize("package_id", ["pkg-07", "pkg-09"])
def test_warning_flags_also_block_approve(ctx: AgentContext, package_id: str) -> None:
    state = _state(ctx, package_id)  # insufficient history; revenue decline
    assert state["risk_flags"] and all(f.severity == "warning" for f in state["risk_flags"])
    package = load_package(SETTINGS.packages_dir, package_id)
    draft = state["draft"].model_copy(update={"draft_recommendation": "approve"})
    errors = validate_draft(draft, package, set(), state["risk_flags"], state["checks"])
    assert any("cannot be 'approve'" in e for e in errors)


def test_high_concentration_blocks_approve(ctx: AgentContext) -> None:
    state = _state(ctx, "pkg-08")  # largest customer 48% of revenue
    assert state["financials"].get(2025, "largest_customer_share_pct") == Decimal("48")
    assert "CONCENTRATION_ABOVE_MAX" in {f.code for f in state["risk_flags"]}
    package = load_package(SETTINGS.packages_dir, "pkg-08")
    draft = state["draft"].model_copy(update={"draft_recommendation": "approve"})
    errors = validate_draft(draft, package, set(), state["risk_flags"], state["checks"])
    assert any("cannot be 'approve'" in e for e in errors)


def test_scope_arguments_come_from_state_not_the_model(ctx: AgentContext) -> None:
    run(ctx, "pkg-01")
    run(ctx, "pkg-02")
    evidence = ctx.evidence
    hits = asyncio.run(evidence.retrieve("test-pkg-01", "customer revenue business", k=50))
    assert hits and all(h.doc_id.startswith("pkg-01-") for h in hits)
