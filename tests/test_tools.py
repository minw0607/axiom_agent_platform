"""Deterministic tools: ratios, policy flags and reconciliation.

Three kinds of check:
1. Hand-computed examples, worked out in the comments.
2. Every synthetic package: tools/ratios.py must match the generator's independent
   reference implementation exactly (the 100% ratio exactness gate).
3. Each edge-case package raises the flag it was built to raise, and only that flag.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from axiom.catalog import ground_truth_statements, list_packages, load_ground_truth
from axiom.domain import Figure, FinancialStatements, LoanRequest, SourceRef
from axiom.settings import Settings
from axiom.tools.policy import load_policy, risk_flags
from axiom.tools.ratios import annual_debt_service, compute_ratios
from axiom.tools.reconcile import reconcile

PACKAGES = Settings.from_env().packages_dir
SRC = SourceRef(doc_id="d", line=1)


def fig(v: str | int) -> Figure:
    return Figure(value=Decimal(str(v)), source=SRC)


def statements(
    years: dict[int, dict[str, int]], loan: tuple[int, str, int] = (0, "0", 1)
) -> FinancialStatements:
    amount, rate, amort = loan
    return FinancialStatements(
        years={y: {k: fig(v) for k, v in f.items()} for y, f in years.items()},
        loan=LoanRequest(
            requested_amount=fig(amount),
            interest_rate_pct=fig(rate),
            term_years=fig(amort),
            amortization_years=fig(amort),
        ),
    )


BASE = {
    "revenue": 10_000_000,
    "cost_of_goods_sold": 6_000_000,
    "operating_expenses": 2_000_000,
    "ebitda": 2_000_000,
    "depreciation_amortization": 500_000,
    "interest_expense": 250_000,
    "income_tax": 300_000,
    "net_income": 950_000,
    "total_current_assets": 3_000_000,
    "total_current_liabilities": 2_000_000,
    "total_assets": 8_000_000,
    "total_liabilities": 5_000_000,
    "total_equity": 3_000_000,
    "total_debt": 3_000_000,
    "debt_principal_repaid": 750_000,
}


def values(fs: FinancialStatements) -> dict[tuple[str, int], Decimal | None]:
    return {(r.name, r.year): r.value for r in compute_ratios(fs)}


# 1. Hand-computed examples -------------------------------------------------------


def test_annual_debt_service_matches_hand_calculation() -> None:
    # 1,000,000 at 6% over 5 years: monthly rate 0.005, 60 payments.
    # Payment = 1,000,000 x 0.005 / (1 - 1.005^-60) = 19,332.80; x 12 = 231,993.60
    assert annual_debt_service(Decimal(1_000_000), Decimal(6), Decimal(5)) == Decimal("231993.60")


def test_annual_debt_service_at_zero_rate_is_straight_line() -> None:
    assert annual_debt_service(Decimal(600_000), Decimal(0), Decimal(5)) == Decimal("120000.00")


@pytest.mark.parametrize(
    ("principal", "rate", "amort"), [(-1, 5, 5), (100, -1, 5), (100, 5, 0), (100, 5, "2.5")]
)
def test_annual_debt_service_rejects_bad_inputs(principal: int, rate: int, amort: str) -> None:
    with pytest.raises(ValueError):
        annual_debt_service(Decimal(principal), Decimal(rate), Decimal(str(amort)))


def test_yearly_ratios_by_hand() -> None:
    v = values(statements({2025: BASE}, loan=(1_000_000, "6", 5)))
    assert v[("ebitda_margin", 2025)] == Decimal("0.20")  # 2.0m / 10.0m
    assert v[("current_ratio", 2025)] == Decimal("1.50")  # 3.0m / 2.0m
    assert v[("leverage", 2025)] == Decimal("1.50")  # 3.0m / 2.0m
    assert v[("interest_coverage", 2025)] == Decimal("6.00")  # (2.0m - 0.5m) / 0.25m
    assert v[("debt_to_equity", 2025)] == Decimal("1.00")  # 3.0m / 3.0m
    assert v[("dscr", 2025)] == Decimal("2.00")  # 2.0m / (0.25m + 0.75m)
    # 2.0m / (0.25m + 0.75m + 231,993.60) = 1.6234... -> 1.62
    assert v[("pro_forma_dscr", 2025)] == Decimal("1.62")
    assert v[("pro_forma_leverage", 2025)] == Decimal("2.00")  # (3.0m + 1.0m) / 2.0m


def test_revenue_growth_uses_prior_year() -> None:
    v = values(statements({2024: BASE, 2025: {**BASE, "revenue": 9_000_000}}))
    assert v[("revenue_growth", 2025)] == Decimal("-0.10")
    assert ("revenue_growth", 2024) not in v


def test_rounding_is_half_up() -> None:
    # 1,005 / 1,000,000 ... use 0.125 exactly: 125,000 / 1,000,000
    v = values(statements({2025: {**BASE, "ebitda": 1_250_000, "revenue": 10_000_000}}))
    assert v[("ebitda_margin", 2025)] == Decimal("0.13")  # 0.125 rounds up


def test_zero_denominator_is_undefined_not_zero() -> None:
    results = compute_ratios(statements({2025: {**BASE, "interest_expense": 0}}))
    coverage = next(r for r in results if r.name == "interest_coverage")
    assert coverage.value is None
    assert coverage.note == "undefined: denominator is zero"


def test_missing_input_is_undefined_and_named() -> None:
    year = {k: v for k, v in BASE.items() if k != "total_current_liabilities"}
    results = compute_ratios(statements({2025: year}))
    current = next(r for r in results if r.name == "current_ratio")
    assert current.value is None
    assert current.note == "undefined: missing total_current_liabilities"


def test_every_result_records_formula_and_inputs() -> None:
    for r in compute_ratios(statements({2025: BASE}, loan=(1_000_000, "6", 5))):
        assert r.formula
        assert r.inputs


# 2. Exactness against the generator's reference implementation ------------------

PACKAGE_IDS = [p["package_id"] for p in list_packages(PACKAGES)]


@pytest.mark.parametrize("package_id", PACKAGE_IDS)
def test_ratios_match_reference_exactly(package_id: str) -> None:
    truth = load_ground_truth(PACKAGES, package_id)
    got = {(r.name, r.year): r.value for r in compute_ratios(ground_truth_statements(truth))}
    expected = {
        (e["name"], e["year"]): None if e["value"] is None else Decimal(e["value"])
        for e in truth["expected_ratios"]
    }
    assert got == expected


# 3. Scenario flags --------------------------------------------------------------


@pytest.mark.parametrize("package_id", PACKAGE_IDS)
def test_scenario_raises_the_golden_flags(package_id: str) -> None:
    """The generator derives the golden flags from the policy text on its own; the two
    implementations must agree on every package, flag by flag and year by year."""
    truth = load_ground_truth(PACKAGES, package_id)
    fs = ground_truth_statements(truth)
    flags = risk_flags(fs, compute_ratios(fs), load_policy())
    golden = truth["golden"]["expected_flags"]
    assert sorted((f.code, f.severity.value, f.year) for f in flags) == sorted(
        (g["code"], g["severity"], g["year"]) for g in golden
    )
    assert all(f.policy_ref for f in flags)


def test_negative_ebitda_is_flagged_not_passed() -> None:
    fs = statements({2025: {**BASE, "ebitda": -100_000}}, loan=(500_000, "7", 5))
    codes = {f.code for f in risk_flags(fs, compute_ratios(fs), load_policy())}
    assert "NEGATIVE_EBITDA" in codes
    assert "LEVERAGE_ABOVE_MAX" not in codes


def _with_share(share: int | None) -> FinancialStatements:
    year = dict(BASE)
    if share is not None:
        year["largest_customer_share_pct"] = share
    return statements({2025: year})


@pytest.mark.parametrize(
    ("share", "code"),
    [
        (14, None),
        (25, None),
        (30, "CONCENTRATION_DISCLOSURE"),
        (40, "CONCENTRATION_DISCLOSURE"),
        (41, "CONCENTRATION_ABOVE_MAX"),
        (None, "CONCENTRATION_UNDEFINED"),
    ],
)
def test_concentration_thresholds(share: int | None, code: str | None) -> None:
    fs = _with_share(share)
    codes = {f.code for f in risk_flags(fs, compute_ratios(fs), load_policy())}
    found = {c for c in codes if c.startswith("CONCENTRATION_")}
    assert found == ({code} if code else set())


def test_dscr_floor_is_separate_from_the_minimum() -> None:
    policy = load_policy()
    assert policy["dscr_decline_floor"].value < policy["min_pro_forma_dscr"].value
    assert policy.version == "CP-2026.2"


@pytest.mark.parametrize("package_id", PACKAGE_IDS)
def test_reconciliation_by_scenario(package_id: str) -> None:
    truth = load_ground_truth(PACKAGES, package_id)
    triggered = {
        (c.name, c.year)
        for c in reconcile(ground_truth_statements(truth))
        if c.outcome == "triggered"
    }
    expected = {
        "inconsistent_figures": {("balance_sheet_balances", 2024)},
        "small_imbalance": {("balance_sheet_balances", 2025)},  # off by 3 dollars
    }
    assert triggered == expected.get(truth["scenario"], set())


def test_reconciliation_without_inputs_is_not_evaluated() -> None:
    year = {k: v for k, v in BASE.items() if k != "total_equity"}
    checks = {c.name: c.outcome for c in reconcile(statements({2025: year}))}
    assert checks["balance_sheet_balances"] == "not_evaluated"
