"""Credit policy rule engine: one test per rule, plus the expected findings per scenario."""

from __future__ import annotations

from typing import Any

import pytest

from axiom.agents.base import AgentContext
from axiom.catalog import list_packages, load_ground_truth
from axiom.domain import Citation, Claim, MemoDraft, MemoSection
from axiom.tools import policy_rules
from axiom.tools.policy_rules import RuleInput, evaluate
from conftest import SETTINGS
from test_workflow import run

PACKAGE_IDS = [p["package_id"] for p in list_packages(SETTINGS.packages_dir)]
EXPECTED_TRIGGERED = {
    "healthy": set(),
    "moderate": set(),
    "weak_dscr": set(),
    "leverage_breach": set(),
    "inconsistent_figures": {"FIGURES_RECONCILE"},
    "missing_year": {"THREE_YEARS_OF_STATEMENTS"},
    "concentration": {"CONCENTRATION_SENSITIVITY"},
    "revenue_decline": set(),
    "low_liquidity": set(),
}


@pytest.mark.parametrize("package_id", PACKAGE_IDS)
def test_scenario_findings(ctx: AgentContext, package_id: str) -> None:
    state = run(ctx, package_id)
    findings = {f.rule_id: f for f in state["findings"]}
    assert len(findings) == len(policy_rules.RULES)
    triggered = {r for r, f in findings.items() if f.outcome == "triggered"}
    scenario = load_ground_truth(SETTINGS.packages_dir, package_id)["scenario"]
    assert triggered == EXPECTED_TRIGGERED[scenario]
    # The synthetic applications have no signature, so that rule can never pass.
    assert findings["LOAN_APPLICATION_SIGNED"].outcome == "not_evaluated"
    assert all(f.policy_ref.startswith("CP-") for f in findings.values())


# Rule by rule, on a real state with one thing changed ---------------------------------


def _inputs(ctx: AgentContext, package_id: str, **changes: Any) -> RuleInput:
    state = run(ctx, package_id)
    base = RuleInput(
        package=state["package"],
        financials=state["financials"],
        checks=state["checks"],
        flags=state["risk_flags"],
        draft=state["draft"],
        policy=ctx.policy,
    )
    return RuleInput(**{**base.__dict__, **changes})


def _outcome(x: RuleInput, rule_id: str) -> tuple[str, str]:
    f = next(f for f in evaluate(x) if f.rule_id == rule_id)
    return f.outcome, f.severity.value


def _with_section(draft: MemoDraft, heading: str, *texts: str, cite: str | None = None):  # type: ignore[no-untyped-def]
    citations = (Citation(doc_id=cite, locator="line 5"),) if cite else ()
    sections = tuple(
        MemoSection(
            heading=s.heading, claims=tuple(Claim(text=t, citations=citations) for t in texts)
        )
        if s.heading == heading
        else s
        for s in draft.sections
    )
    return draft.model_copy(update={"sections": sections})


def test_an_unnamed_flag_is_triggered(ctx: AgentContext) -> None:
    x = _inputs(ctx, "pkg-05")
    draft = _with_section(x.draft, "Risk factors", "Leverage is high.", cite="pkg-05-balance_sheet")
    assert _outcome(RuleInput(**{**x.__dict__, "draft": draft}), "FLAGS_NAMED") == (
        "triggered",
        "high",
    )


def test_an_exception_needs_a_stated_condition(ctx: AgentContext) -> None:
    x = _inputs(ctx, "pkg-05")
    assert _outcome(x, "EXCEPTION_CONDITIONS")[0] == "passed"
    # The label alone contains "conditions"; it must not count as stating one.
    draft = _with_section(x.draft, "Draft recommendation", "Draft: approve_with_conditions.")
    assert _outcome(RuleInput(**{**x.__dict__, "draft": draft}), "EXCEPTION_CONDITIONS") == (
        "triggered",
        "high",
    )


def test_below_the_dscr_floor_must_decline(ctx: AgentContext) -> None:
    x = _inputs(ctx, "pkg-04")
    assert _outcome(x, "DSCR_FLOOR")[0] == "passed"
    draft = x.draft.model_copy(update={"draft_recommendation": "approve_with_conditions"})
    assert _outcome(RuleInput(**{**x.__dict__, "draft": draft}), "DSCR_FLOOR") == (
        "triggered",
        "high",
    )


def test_concentration_must_be_stated_and_cited(ctx: AgentContext) -> None:
    x = _inputs(ctx, "pkg-08")
    assert _outcome(x, "CONCENTRATION_IDENTIFIED")[0] == "passed"
    bd = "pkg-08-business_description"
    uncited = _with_section(x.draft, "Risk factors", "CONCENTRATION_ABOVE_MAX: 48% of revenue.")
    no_terms = _with_section(
        x.draft, "Risk factors", "CONCENTRATION_ABOVE_MAX: 48% of revenue.", cite=bd
    )
    assert _outcome(RuleInput(**{**x.__dict__, "draft": uncited}), "CONCENTRATION_IDENTIFIED") == (
        "triggered",
        "high",
    )
    assert _outcome(RuleInput(**{**x.__dict__, "draft": no_terms}), "CONCENTRATION_IDENTIFIED") == (
        "triggered",
        "warning",
    )


def test_unknown_concentration_is_not_evaluated(ctx: AgentContext) -> None:
    x = _inputs(ctx, "pkg-01")
    years = {
        y: {k: v for k, v in f.items() if k != "largest_customer_share_pct"}
        for y, f in x.financials.years.items()
    }
    fs = x.financials.model_copy(update={"years": years})
    y = RuleInput(**{**x.__dict__, "financials": fs})
    assert _outcome(y, "CONCENTRATION_IDENTIFIED")[0] == "not_evaluated"
    assert _outcome(y, "CONCENTRATION_SENSITIVITY")[0] == "not_evaluated"


@pytest.mark.parametrize(
    "text",
    [
        "The owner is 67 years old and close to retirement.",
        "The founders are married and share management duties.",
        "Its owners' religious community supplies most customers.",
    ],
)
def test_protected_characteristics_are_flagged(ctx: AgentContext, text: str) -> None:
    x = _inputs(ctx, "pkg-01")
    draft = _with_section(x.draft, "Borrower and request", text, cite="pkg-01-business_description")
    assert _outcome(
        RuleInput(**{**x.__dict__, "draft": draft}), "NO_PROTECTED_CHARACTERISTICS"
    ) == (
        "triggered",
        "high",
    )


def test_ordinary_business_language_is_not_flagged(ctx: AgentContext) -> None:
    x = _inputs(ctx, "pkg-01")
    text = "Management has operated the business for more than five years; owners guarantee debt."
    draft = _with_section(x.draft, "Borrower and request", text, cite="pkg-01-business_description")
    assert (
        _outcome(RuleInput(**{**x.__dict__, "draft": draft}), "NO_PROTECTED_CHARACTERISTICS")[0]
        == "passed"
    )


def test_final_decision_language_is_flagged(ctx: AgentContext) -> None:
    x = _inputs(ctx, "pkg-01")
    draft = _with_section(x.draft, "Draft recommendation", "The loan has been approved.")
    assert _outcome(RuleInput(**{**x.__dict__, "draft": draft}), "NO_FINAL_DECISION") == (
        "triggered",
        "high",
    )


def test_missing_document_and_out_of_scope_amount(ctx: AgentContext) -> None:
    x = _inputs(ctx, "pkg-01")
    package = x.package.model_copy(
        update={"documents": tuple(d for d in x.package.documents if d.kind != "cash_flow")}
    )
    assert (
        _outcome(RuleInput(**{**x.__dict__, "package": package}), "REQUIRED_DOCUMENTS")[0]
        == "triggered"
    )
    loan = x.financials.loan
    big = loan.model_copy(
        update={"requested_amount": loan.requested_amount.model_copy(update={"value": 9_000_000})}
    )
    fs = x.financials.model_copy(update={"loan": big})
    assert _outcome(RuleInput(**{**x.__dict__, "financials": fs}), "LOAN_AMOUNT_IN_SCOPE") == (
        "triggered",
        "high",
    )


def test_a_rule_that_crashes_is_not_evaluated(
    ctx: AgentContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(x: RuleInput):  # type: ignore[no-untyped-def]
        raise KeyError("boom")

    monkeypatch.setattr(policy_rules, "RULES", (broken,))
    findings = evaluate(_inputs(ctx, "pkg-01"))
    assert [(f.rule_id, f.outcome) for f in findings] == [("BROKEN", "not_evaluated")]
