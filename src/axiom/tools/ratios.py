"""Deterministic financial ratios.

Plain Decimal arithmetic, no model involvement. Each result records its formula and
inputs so a reviewer can recompute it. Ratios round half-up to 2 decimal places; a
ratio with a zero or missing denominator is reported as undefined (value None), never
as zero or infinity.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from axiom.domain import FinancialStatements, RatioResult

TWO_PLACES = Decimal("0.01")
MONTHS_PER_YEAR = 12


def _round(value: Decimal) -> Decimal:
    return value.quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


def _ratio(
    name: str,
    year: int,
    formula: str,
    numerator: Decimal | None,
    denominator: Decimal | None,
    inputs: dict[str, Decimal | None],
    note: str | None = None,
) -> RatioResult:
    known = {k: v for k, v in inputs.items() if v is not None}
    missing = sorted(k for k, v in inputs.items() if v is None)
    if missing:
        return RatioResult(
            name=name,
            year=year,
            value=None,
            formula=formula,
            inputs=known,
            note=f"undefined: missing {', '.join(missing)}",
        )
    assert numerator is not None and denominator is not None
    if denominator == 0:
        return RatioResult(
            name=name,
            year=year,
            value=None,
            formula=formula,
            inputs=known,
            note="undefined: denominator is zero",
        )
    return RatioResult(
        name=name,
        year=year,
        value=_round(numerator / denominator),
        formula=formula,
        inputs=known,
        note=note,
    )


def _sum(*values: Decimal | None) -> Decimal | None:
    total = Decimal(0)
    for v in values:
        if v is None:
            return None
        total += v
    return total


def _sub(a: Decimal | None, b: Decimal | None) -> Decimal | None:
    return None if a is None or b is None else a - b


def annual_debt_service(
    principal: Decimal, rate_pct: Decimal, amortization_years: Decimal
) -> Decimal:
    """Twelve level monthly payments on a fully amortizing loan.

    The monthly payment is rounded to the cent first, as a lender would bill it.
    """
    if principal < 0 or rate_pct < 0 or amortization_years <= 0:
        raise ValueError("principal and rate must be non-negative; amortization must be positive")
    if amortization_years != amortization_years.to_integral_value():
        raise ValueError("amortization_years must be a whole number")
    months = int(amortization_years) * MONTHS_PER_YEAR
    if rate_pct == 0:
        monthly = principal / months
    else:
        r = rate_pct / Decimal(100) / MONTHS_PER_YEAR
        monthly = principal * r / (1 - (1 + r) ** -months)
    return _round(monthly) * MONTHS_PER_YEAR


def yearly_ratios(fs: FinancialStatements, year: int) -> list[RatioResult]:
    def g(field: str) -> Decimal | None:
        return fs.get(year, field)

    revenue, ebitda = g("revenue"), g("ebitda")
    interest, da = g("interest_expense"), g("depreciation_amortization")
    principal_repaid, total_debt = g("debt_principal_repaid"), g("total_debt")
    tca, tcl, equity = g("total_current_assets"), g("total_current_liabilities"), g("total_equity")

    ebit = _sub(ebitda, da)
    debt_service = _sum(interest, principal_repaid)
    results = [
        _ratio(
            "ebitda_margin",
            year,
            "ebitda / revenue",
            ebitda,
            revenue,
            {"ebitda": ebitda, "revenue": revenue},
        ),
        _ratio(
            "current_ratio",
            year,
            "total_current_assets / total_current_liabilities",
            tca,
            tcl,
            {"total_current_assets": tca, "total_current_liabilities": tcl},
        ),
        _ratio(
            "leverage",
            year,
            "total_debt / ebitda",
            total_debt,
            ebitda,
            {"total_debt": total_debt, "ebitda": ebitda},
        ),
        _ratio(
            "interest_coverage",
            year,
            "(ebitda - depreciation_amortization) / interest_expense",
            ebit,
            interest,
            {"ebitda": ebitda, "depreciation_amortization": da, "interest_expense": interest},
        ),
        _ratio(
            "debt_to_equity",
            year,
            "total_debt / total_equity",
            total_debt,
            equity,
            {"total_debt": total_debt, "total_equity": equity},
        ),
        _ratio(
            "dscr",
            year,
            "ebitda / (interest_expense + debt_principal_repaid)",
            ebitda,
            debt_service,
            {
                "ebitda": ebitda,
                "interest_expense": interest,
                "debt_principal_repaid": principal_repaid,
            },
        ),
    ]
    prior = year - 1
    if prior in fs.years:
        prior_revenue = fs.get(prior, "revenue")
        results.append(
            _ratio(
                "revenue_growth",
                year,
                "(revenue - prior_revenue) / prior_revenue",
                _sub(revenue, prior_revenue),
                prior_revenue,
                {"revenue": revenue, "prior_revenue": prior_revenue},
            )
        )
    return results


def pro_forma_ratios(fs: FinancialStatements) -> list[RatioResult]:
    """Coverage and leverage for the latest year, including the requested loan.

    Assumes existing debt service continues unchanged alongside the new loan.
    """
    year = fs.fiscal_years()[-1]
    loan = fs.loan
    ads = annual_debt_service(
        loan.requested_amount.value, loan.interest_rate_pct.value, loan.amortization_years.value
    )
    ebitda = fs.get(year, "ebitda")
    interest = fs.get(year, "interest_expense")
    principal_repaid = fs.get(year, "debt_principal_repaid")
    total_debt = fs.get(year, "total_debt")
    requested = loan.requested_amount.value
    note = "assumes existing debt service continues alongside the requested loan"
    return [
        RatioResult(
            name="proposed_annual_debt_service",
            year=year,
            value=ads,
            formula="12 x level monthly payment (rounded to the cent) on requested_amount "
            "at interest_rate_pct over amortization_years",
            inputs={
                "requested_amount": requested,
                "interest_rate_pct": loan.interest_rate_pct.value,
                "amortization_years": loan.amortization_years.value,
            },
        ),
        _ratio(
            "pro_forma_dscr",
            year,
            "ebitda / (interest_expense + debt_principal_repaid + proposed_annual_debt_service)",
            ebitda,
            _sum(interest, principal_repaid, ads),
            {
                "ebitda": ebitda,
                "interest_expense": interest,
                "debt_principal_repaid": principal_repaid,
                "proposed_annual_debt_service": ads,
            },
            note=note,
        ),
        _ratio(
            "pro_forma_leverage",
            year,
            "(total_debt + requested_amount) / ebitda",
            _sum(total_debt, requested),
            ebitda,
            {"total_debt": total_debt, "requested_amount": requested, "ebitda": ebitda},
        ),
    ]


def compute_ratios(fs: FinancialStatements) -> list[RatioResult]:
    """All yearly ratios for every fiscal year, then the pro forma ratios."""
    results: list[RatioResult] = []
    for year in fs.fiscal_years():
        results.extend(yearly_ratios(fs, year))
    results.extend(pro_forma_ratios(fs))
    return results
