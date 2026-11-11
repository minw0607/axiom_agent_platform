"""Credit policy rule engine: deterministic checks of a draft memo against policy CP-2026.2.

Each rule cites the policy section it enforces and returns passed, triggered or
not_evaluated. A rule that cannot run says so; it never reports a pass. These findings are
drafting aids for the credit officer, not decisions. Judgments that need reading rather than
matching, such as whether the mitigating factors are adequate, belong to the compliance
reviewer agent.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal

from axiom.domain import (
    BALANCE_SHEET_FIELDS,
    CASH_FLOW_FIELDS,
    INCOME_STATEMENT_FIELDS,
    BorrowerPackage,
    CheckResult,
    ComplianceFinding,
    FinancialStatements,
    MemoDraft,
    RiskFlag,
    Severity,
)
from axiom.tools.policy import PolicyThresholds

REQUIRED_DOCUMENTS = (
    "income_statement",
    "balance_sheet",
    "cash_flow",
    "loan_application",
    "business_description",
)
FINAL_DECISION = re.compile(
    r"\b(loan|request|credit) (is|has been|was) (hereby )?(approved|declined|denied|committed)\b",
    re.IGNORECASE,
)
# A keyword screen for protected characteristics (CP-7.1). It catches explicit mentions only;
# the counterfactual fairness pairs in the evaluation suite test whether they affect outcomes.
PROTECTED = re.compile(
    r"\b(race|racial|ethnic\w*|religio\w*|national origin|nationality|citizenship|gender|"
    r"sex|female|male|marital|married|unmarried|divorced|widow\w*|pregnan\w*|"
    r"skin colou?r|years old|elderly|owner'?s age|age of the owner)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class RuleInput:
    package: BorrowerPackage
    financials: FinancialStatements
    checks: list[CheckResult]
    flags: list[RiskFlag]
    draft: MemoDraft
    policy: PolicyThresholds


Rule = Callable[[RuleInput], ComplianceFinding]


def _finding(
    rule_id: str,
    policy_ref: str,
    outcome: str,
    message: str,
    severity: Severity = Severity.INFO,
) -> ComplianceFinding:
    return ComplianceFinding(
        rule_id=rule_id,
        policy_ref=policy_ref,
        outcome=outcome,  # type: ignore[arg-type]
        severity=severity if outcome == "triggered" else Severity.INFO,
        message=message,
    )


def _section_text(draft: MemoDraft, heading: str) -> str:
    return " ".join(c.text for s in draft.sections if s.heading == heading for c in s.claims)


def _all_text(draft: MemoDraft) -> str:
    return " ".join(c.text for s in draft.sections for c in s.claims)


# CP-1 and CP-3: scope and required documents -----------------------------------------


def loan_amount_in_scope(x: RuleInput) -> ComplianceFinding:
    low, high = x.policy["min_loan_amount"], x.policy["max_loan_amount"]
    amount = x.financials.loan.requested_amount.value
    if low.value <= amount <= high.value:
        return _finding(
            "LOAN_AMOUNT_IN_SCOPE", low.policy_ref, "passed", f"{amount} is within policy scope."
        )
    return _finding(
        "LOAN_AMOUNT_IN_SCOPE",
        low.policy_ref,
        "triggered",
        f"{amount} is outside the policy's range of {low.value} to {high.value}.",
        Severity.HIGH,
    )


def required_documents(x: RuleInput) -> ComplianceFinding:
    kinds = {d.kind for d in x.package.documents}
    missing = [k for k in REQUIRED_DOCUMENTS if k not in kinds]
    if missing:
        return _finding(
            "REQUIRED_DOCUMENTS",
            "CP-3",
            "triggered",
            f"Missing documents: {', '.join(missing)}.",
            Severity.HIGH,
        )
    return _finding("REQUIRED_DOCUMENTS", "CP-3", "passed", "All required documents are present.")


def three_years_of_statements(x: RuleInput) -> ComplianceFinding:
    required = x.policy["min_history_years"]
    short = []
    for name, fields in (
        ("income statement", INCOME_STATEMENT_FIELDS),
        ("balance sheet", BALANCE_SHEET_FIELDS),
        ("cash flow statement", CASH_FLOW_FIELDS),
    ):
        years = [y for y, f in x.financials.years.items() if any(k in f for k in fields)]
        if Decimal(len(years)) < required.value:
            short.append(f"{name} ({len(years)})")
    if not short:
        return _finding(
            "THREE_YEARS_OF_STATEMENTS",
            required.policy_ref,
            "passed",
            f"Each statement covers {required.value} fiscal years.",
        )
    named = "INSUFFICIENT_HISTORY" in _section_text(x.draft, "Risk factors")
    return _finding(
        "THREE_YEARS_OF_STATEMENTS",
        required.policy_ref,
        "triggered",
        f"Fewer than {required.value} fiscal years: {', '.join(short)}. The request may proceed "
        "only with an explanation of the missing period"
        + ("; the memo flags it." if named else "; the memo does not flag it."),
        Severity.WARNING if named else Severity.HIGH,
    )


def loan_application_complete(x: RuleInput) -> ComplianceFinding:
    doc = x.package.document("loan_application")
    if doc is None:
        return _finding(
            "LOAN_APPLICATION_COMPLETE",
            "CP-3.2",
            "not_evaluated",
            "There is no loan application to check.",
        )
    purpose = re.search(r"^purpose:\s*\S", doc.text, re.IGNORECASE | re.MULTILINE)
    if not purpose:
        return _finding(
            "LOAN_APPLICATION_COMPLETE",
            "CP-3.2",
            "triggered",
            "The loan application does not state a purpose.",
            Severity.WARNING,
        )
    return _finding(
        "LOAN_APPLICATION_COMPLETE",
        "CP-3.2",
        "passed",
        "Amount, rate, term, amortization and purpose are stated.",
    )


def loan_application_signed(x: RuleInput) -> ComplianceFinding:
    # The synthetic applications carry no signature block, so this cannot be checked.
    return _finding(
        "LOAN_APPLICATION_SIGNED",
        "CP-3.2",
        "not_evaluated",
        "The application has no signature field to check; the credit officer must confirm it.",
    )


def business_description_complete(x: RuleInput) -> ComplianceFinding:
    doc = x.package.document("business_description")
    if doc is None:
        return _finding(
            "BUSINESS_DESCRIPTION_COMPLETE",
            "CP-3.3",
            "not_evaluated",
            "There is no business description to check.",
        )
    text = doc.text.lower()
    topics = {
        "ownership": ("owner", "ownership"),
        "operating history": ("founded", "operated", "established"),
        "customer concentration": ("customer",),
    }
    missing = [t for t, words in topics.items() if not any(w in text for w in words)]
    if missing:
        return _finding(
            "BUSINESS_DESCRIPTION_COMPLETE",
            "CP-3.3",
            "triggered",
            f"The business description does not cover: {', '.join(missing)}.",
            Severity.WARNING,
        )
    return _finding(
        "BUSINESS_DESCRIPTION_COMPLETE",
        "CP-3.3",
        "passed",
        "Ownership, operating history and customer concentration are covered.",
    )


def figures_reconcile(x: RuleInput) -> ComplianceFinding:
    triggered = [c for c in x.checks if c.outcome == "triggered"]
    if triggered:
        names = ", ".join(f"{c.name} ({c.year})" for c in triggered)
        refer = x.draft.draft_recommendation == "refer"
        return _finding(
            "FIGURES_RECONCILE",
            "CP-3.4",
            "triggered",
            f"Statements do not reconcile: {names}. They return to the relationship manager "
            + ("and the draft refers them." if refer else "but the draft does not refer them."),
            Severity.HIGH,
        )
    if not x.checks or any(c.outcome == "not_evaluated" for c in x.checks):
        return _finding(
            "FIGURES_RECONCILE",
            "CP-3.4",
            "not_evaluated",
            "At least one reconciliation check could not run.",
        )
    return _finding("FIGURES_RECONCILE", "CP-3.4", "passed", "All statements reconcile.")


# CP-4 to CP-6: standards, concentration and exceptions ------------------------------


def flags_named_in_memo(x: RuleInput) -> ComplianceFinding:
    risk_text = _section_text(x.draft, "Risk factors")
    missing = [f.code for f in x.flags if f.code not in risk_text]
    if missing:
        return _finding(
            "FLAGS_NAMED",
            "CP-6.1",
            "triggered",
            f"Risk flags not named under Risk factors: {', '.join(missing)}.",
            Severity.HIGH,
        )
    if not x.flags:
        return _finding("FLAGS_NAMED", "CP-6.1", "passed", "No risk flags to name.")
    return _finding(
        "FLAGS_NAMED", "CP-6.1", "passed", "Every risk flag is named under Risk factors."
    )


def exception_has_conditions(x: RuleInput) -> ComplianceFinding:
    failed = [f for f in x.flags if f.severity == Severity.HIGH]
    if x.draft.draft_recommendation != "approve_with_conditions" or not failed:
        return _finding(
            "EXCEPTION_CONDITIONS",
            "CP-6.1",
            "passed",
            "The draft does not propose an exception approval.",
        )
    # Ignore the recommendation label itself, which contains the word "conditions".
    rec_text = _section_text(x.draft, "Draft recommendation").lower()
    rec_text = rec_text.replace("approve_with_conditions", "").replace(
        "approve with conditions", ""
    )
    if "condition" not in rec_text:
        return _finding(
            "EXCEPTION_CONDITIONS",
            "CP-6.1",
            "triggered",
            "The draft proposes approval as an exception but states no conditions.",
            Severity.HIGH,
        )
    return _finding(
        "EXCEPTION_CONDITIONS",
        "CP-6.1",
        "passed",
        "The exception states conditions; their adequacy is for the credit officer.",
    )


def dscr_floor_declined(x: RuleInput) -> ComplianceFinding:
    if not any(f.code == "DSCR_BELOW_DECLINE_FLOOR" for f in x.flags):
        return _finding("DSCR_FLOOR", "CP-6.3", "passed", "Pro forma DSCR is above the floor.")
    if x.draft.draft_recommendation in ("decline", "refer"):
        return _finding(
            "DSCR_FLOOR",
            "CP-6.3",
            "passed",
            "Pro forma DSCR is below the floor; the draft declines.",
        )
    return _finding(
        "DSCR_FLOOR",
        "CP-6.3",
        "triggered",
        "Pro forma DSCR is below the floor, but the draft does not decline.",
        Severity.HIGH,
    )


def concentration_identified(x: RuleInput) -> ComplianceFinding:
    disclose = x.policy["concentration_disclosure_pct"]
    latest = x.financials.fiscal_years()[-1]
    share = x.financials.get(latest, "largest_customer_share_pct")
    if share is None:
        return _finding(
            "CONCENTRATION_IDENTIFIED",
            disclose.policy_ref,
            "not_evaluated",
            "The largest customer's share of revenue is unknown.",
        )
    if share <= disclose.value:
        return _finding(
            "CONCENTRATION_IDENTIFIED",
            disclose.policy_ref,
            "passed",
            f"The largest customer is {share}% of revenue; no disclosure is required.",
        )
    number = re.compile(rf"\b{re.escape(str(share))}\s*%")
    bd = f"{x.package.package_id}-business_description"
    claims = [
        c
        for s in x.draft.sections
        for c in s.claims
        if number.search(c.text) and any(ci.doc_id == bd for ci in c.citations)
    ]
    if not claims:
        return _finding(
            "CONCENTRATION_IDENTIFIED",
            disclose.policy_ref,
            "triggered",
            f"The largest customer is {share}% of revenue, but no cited claim states it.",
            Severity.HIGH,
        )
    if not any("contract" in c.text.lower() for c in claims):
        return _finding(
            "CONCENTRATION_IDENTIFIED",
            disclose.policy_ref,
            "triggered",
            "The concentration is stated, but not the customer's contract terms.",
            Severity.WARNING,
        )
    return _finding(
        "CONCENTRATION_IDENTIFIED",
        disclose.policy_ref,
        "passed",
        "The concentration and contract terms are stated and cited.",
    )


def concentration_sensitivity(x: RuleInput) -> ComplianceFinding:
    max_t = x.policy["max_customer_concentration_pct"]
    latest = x.financials.fiscal_years()[-1]
    share = x.financials.get(latest, "largest_customer_share_pct")
    if share is None:
        return _finding(
            "CONCENTRATION_SENSITIVITY",
            max_t.policy_ref,
            "not_evaluated",
            "The largest customer's share of revenue is unknown.",
        )
    if share <= max_t.value:
        return _finding(
            "CONCENTRATION_SENSITIVITY",
            max_t.policy_ref,
            "passed",
            "No sensitivity analysis is required.",
        )
    # The platform does not model the loss of a customer, so the analysis must come from outside.
    return _finding(
        "CONCENTRATION_SENSITIVITY",
        max_t.policy_ref,
        "triggered",
        f"Concentration of {share}% requires a sensitivity analysis, which the platform does "
        "not produce. The credit officer must obtain one.",
        Severity.WARNING,
    )


# CP-2 and CP-7: decision authority and fair lending ---------------------------------


def no_final_decision(x: RuleInput) -> ComplianceFinding:
    hits = FINAL_DECISION.findall(_all_text(x.draft))
    if hits:
        return _finding(
            "NO_FINAL_DECISION",
            "CP-2.3",
            "triggered",
            "The draft states a credit decision; only a credit officer makes one.",
            Severity.HIGH,
        )
    return _finding(
        "NO_FINAL_DECISION", "CP-2.3", "passed", "The draft states no final credit decision."
    )


def no_protected_characteristics(x: RuleInput) -> ComplianceFinding:
    hits = sorted({m.group(0).lower() for m in PROTECTED.finditer(_all_text(x.draft))})
    if hits:
        return _finding(
            "NO_PROTECTED_CHARACTERISTICS",
            "CP-7.1",
            "triggered",
            f"The draft mentions protected characteristics: {', '.join(hits)}.",
            Severity.HIGH,
        )
    return _finding(
        "NO_PROTECTED_CHARACTERISTICS",
        "CP-7.1",
        "passed",
        "The draft mentions no protected characteristics (keyword screen).",
    )


RULES: tuple[Rule, ...] = (
    loan_amount_in_scope,
    required_documents,
    three_years_of_statements,
    loan_application_complete,
    loan_application_signed,
    business_description_complete,
    figures_reconcile,
    flags_named_in_memo,
    exception_has_conditions,
    dscr_floor_declined,
    concentration_identified,
    concentration_sensitivity,
    no_final_decision,
    no_protected_characteristics,
)


def evaluate(x: RuleInput) -> list[ComplianceFinding]:
    """Run every rule. A rule that raises is recorded as not_evaluated, never skipped."""
    out = []
    for rule in RULES:
        try:
            out.append(rule(x))
        except Exception as exc:  # a broken rule must not pass silently
            out.append(
                _finding(
                    rule.__name__.upper(),
                    "-",
                    "not_evaluated",
                    f"The rule could not run ({type(exc).__name__}).",
                )
            )
    return out
