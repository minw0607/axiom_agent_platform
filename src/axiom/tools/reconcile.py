"""Reconciliation checks on extracted statements (deterministic).

They catch extraction errors and internally inconsistent packages. A check whose inputs
are missing reports not_evaluated, never passed.
"""

from __future__ import annotations

from decimal import Decimal

from axiom.domain import CheckResult, FinancialStatements

TOLERANCE = Decimal("1")  # figures are whole dollars


def _check(
    name: str,
    year: int,
    left_label: str,
    left: Decimal | None,
    right_label: str,
    right: Decimal | None,
) -> CheckResult:
    if left is None or right is None:
        return CheckResult(name=name, year=year, outcome="not_evaluated", detail="inputs missing")
    diff = left - right
    if abs(diff) <= TOLERANCE:
        return CheckResult(
            name=name, year=year, outcome="passed", detail=f"{left_label} equals {right_label}"
        )
    return CheckResult(
        name=name,
        year=year,
        outcome="triggered",
        detail=f"{left_label} {left} differs from {right_label} {right} by {diff}",
    )


def reconcile(fs: FinancialStatements) -> list[CheckResult]:
    results: list[CheckResult] = []
    for year in fs.fiscal_years():

        def g(field: str, year: int = year) -> Decimal | None:
            return fs.get(year, field)

        rev, cogs, opex = g("revenue"), g("cost_of_goods_sold"), g("operating_expenses")
        ebitda, da, interest, tax = (
            g("ebitda"),
            g("depreciation_amortization"),
            g("interest_expense"),
            g("income_tax"),
        )
        results.append(
            _check(
                "ebitda_foots",
                year,
                "reported EBITDA",
                ebitda,
                "revenue - COGS - operating expenses",
                None if None in (rev, cogs, opex) else rev - cogs - opex,  # type: ignore[operator]
            )
        )
        results.append(
            _check(
                "net_income_foots",
                year,
                "reported net income",
                g("net_income"),
                "EBITDA - D&A - interest - tax",
                None if None in (ebitda, da, interest, tax) else ebitda - da - interest - tax,  # type: ignore[operator]
            )
        )
        tl, eq = g("total_liabilities"), g("total_equity")
        results.append(
            _check(
                "balance_sheet_balances",
                year,
                "total assets",
                g("total_assets"),
                "total liabilities + equity",
                None if tl is None or eq is None else tl + eq,
            )
        )
    return results
