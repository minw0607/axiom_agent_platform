"""Generate synthetic borrower packages with known ground truth.

Numbers first, text second: each package starts from a small set of parameters, every
line item is derived from them in whole dollars, and the documents are rendered from
those numbers. The ground truth records each figure's value and the line it appears on,
plus expected ratios computed here, independently of src/axiom/tools/ratios.py, so the
two implementations check each other.

All borrowers are fictional. Output: data/synthetic/packages/<package_id>/ and
data/synthetic/packages/catalog.json. Re-running produces identical files.

    uv run python scripts/generate_synthetic.py
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "data" / "synthetic" / "packages"
YEARS = (2023, 2024, 2025)
D = Decimal


@dataclass
class Params:
    package_id: str
    industry: str
    scenario: str
    revenue0: int
    growth: tuple[float, ...]  # one per year after the first
    cogs_pct: float
    opex_pct: float
    da: int
    debt: int
    debt_rate: float
    principal_repaid: int
    tax_rate: float
    cash_pct: float
    ar_pct: float
    inv_pct: float
    other_ca_pct: float
    fixed_assets: int
    cl_pct: float
    loan: int
    loan_rate: str
    term: int
    amort: int
    years: tuple[int, ...] = YEARS
    top_customer_pct: int = 14
    founded: int = 2004
    employees: int = 60
    imbalance: dict[int, int] = field(default_factory=dict)  # year -> assets overstated by


PACKAGES = [
    Params(
        "pkg-01",
        "precision machining",
        "healthy",
        12_400_000,
        (0.06, 0.05),
        0.62,
        0.21,
        420_000,
        3_200_000,
        0.065,
        600_000,
        0.24,
        0.08,
        0.13,
        0.11,
        0.02,
        4_100_000,
        0.17,
        1_500_000,
        "7.25",
        7,
        7,
    ),
    Params(
        "pkg-02",
        "food distribution",
        "healthy",
        28_900_000,
        (0.04, 0.05),
        0.78,
        0.13,
        380_000,
        4_800_000,
        0.07,
        800_000,
        0.25,
        0.05,
        0.11,
        0.09,
        0.01,
        3_200_000,
        0.15,
        2_000_000,
        "7.50",
        5,
        5,
        employees=140,
    ),
    Params(
        "pkg-03",
        "commercial landscaping",
        "moderate",
        6_700_000,
        (0.03, 0.02),
        0.58,
        0.28,
        310_000,
        2_400_000,
        0.075,
        420_000,
        0.25,
        0.06,
        0.14,
        0.03,
        0.02,
        2_300_000,
        0.19,
        900_000,
        "8.00",
        5,
        5,
        employees=85,
    ),
    Params(
        "pkg-04",
        "specialty printing",
        "weak_dscr",
        9_100_000,
        (0.01, -0.03),
        0.64,
        0.245,
        360_000,
        2_600_000,
        0.08,
        650_000,
        0.25,
        0.05,
        0.12,
        0.08,
        0.02,
        2_900_000,
        0.18,
        1_400_000,
        "8.25",
        5,
        5,
    ),
    Params(
        "pkg-05",
        "regional trucking",
        "leverage_breach",
        15_600_000,
        (0.05, 0.04),
        0.66,
        0.21,
        900_000,
        7_800_000,
        0.075,
        500_000,
        0.25,
        0.05,
        0.12,
        0.02,
        0.02,
        9_800_000,
        0.16,
        3_000_000,
        "7.75",
        7,
        10,
        employees=110,
    ),
    Params(
        "pkg-06",
        "HVAC contracting",
        "inconsistent_figures",
        11_200_000,
        (0.05, 0.06),
        0.63,
        0.23,
        280_000,
        2_100_000,
        0.07,
        450_000,
        0.25,
        0.07,
        0.15,
        0.06,
        0.02,
        2_000_000,
        0.18,
        1_200_000,
        "7.50",
        5,
        5,
        imbalance={2024: 50_000},
    ),
    Params(
        "pkg-07",
        "craft brewing",
        "missing_year",
        4_300_000,
        (0.12,),
        0.52,
        0.31,
        260_000,
        1_300_000,
        0.08,
        240_000,
        0.25,
        0.07,
        0.08,
        0.14,
        0.02,
        2_600_000,
        0.16,
        700_000,
        "8.50",
        7,
        7,
        years=(2024, 2025),
        founded=2019,
        employees=28,
    ),
    Params(
        "pkg-08",
        "contract packaging",
        "concentration",
        19_800_000,
        (0.07, 0.06),
        0.67,
        0.18,
        520_000,
        3_600_000,
        0.07,
        700_000,
        0.25,
        0.06,
        0.16,
        0.09,
        0.02,
        4_400_000,
        0.18,
        2_500_000,
        "7.50",
        7,
        7,
        top_customer_pct=48,
        employees=120,
    ),
    Params(
        "pkg-09",
        "retail furniture",
        "revenue_decline",
        13_500_000,
        (0.02, -0.14),
        0.58,
        0.30,
        300_000,
        2_000_000,
        0.075,
        420_000,
        0.25,
        0.06,
        0.05,
        0.22,
        0.02,
        2_200_000,
        0.19,
        800_000,
        "8.00",
        5,
        5,
        employees=75,
    ),
    Params(
        "pkg-10",
        "software reselling",
        "low_liquidity",
        8_800_000,
        (0.05, 0.04),
        0.60,
        0.25,
        150_000,
        1_400_000,
        0.07,
        300_000,
        0.25,
        0.02,
        0.09,
        0.02,
        0.01,
        1_600_000,
        0.13,
        600_000,
        "7.75",
        3,
        3,
        employees=40,
    ),
]

LABELS = {
    "revenue": "Revenue",
    "cost_of_goods_sold": "Cost of goods sold",
    "operating_expenses": "Operating expenses",
    "ebitda": "EBITDA",
    "depreciation_amortization": "Depreciation and amortization",
    "interest_expense": "Interest expense",
    "income_tax": "Income tax expense",
    "net_income": "Net income",
    "cash": "Cash and equivalents",
    "accounts_receivable": "Accounts receivable",
    "inventory": "Inventory",
    "total_current_assets": "Total current assets",
    "total_assets": "Total assets",
    "total_current_liabilities": "Total current liabilities",
    "total_debt": "Total debt (bank and term loans)",
    "total_liabilities": "Total liabilities",
    "total_equity": "Total equity",
    "capital_expenditures": "Capital expenditures",
    "debt_principal_repaid": "Repayment of long-term debt",
}


def r(x: float) -> int:
    return int(D(str(x)).quantize(D("1"), rounding=ROUND_HALF_UP))


def build_figures(p: Params) -> dict[int, dict[str, int]]:
    out: dict[int, dict[str, int]] = {}
    revenue = float(p.revenue0)
    for i, year in enumerate(p.years):
        if i > 0:
            revenue *= 1 + p.growth[i - 1]
        rev = r(revenue)
        cogs, opex = r(rev * p.cogs_pct), r(rev * p.opex_pct)
        ebitda = rev - cogs - opex
        debt = p.debt - p.principal_repaid * i
        interest = r(debt * p.debt_rate)
        pretax = ebitda - p.da - interest
        tax = max(0, r(pretax * p.tax_rate))
        net = pretax - tax
        cash, ar, inv = r(rev * p.cash_pct), r(rev * p.ar_pct), r(rev * p.inv_pct)
        tca = cash + ar + inv + r(rev * p.other_ca_pct)
        total_assets = tca + p.fixed_assets
        tcl = r(rev * p.cl_pct)
        total_liabilities = tcl + debt
        equity = total_assets - total_liabilities
        total_assets += p.imbalance.get(year, 0)
        out[year] = {
            "revenue": rev,
            "cost_of_goods_sold": cogs,
            "operating_expenses": opex,
            "ebitda": ebitda,
            "depreciation_amortization": p.da,
            "interest_expense": interest,
            "income_tax": tax,
            "net_income": net,
            "cash": cash,
            "accounts_receivable": ar,
            "inventory": inv,
            "total_current_assets": tca,
            "total_assets": total_assets,
            "total_current_liabilities": tcl,
            "total_debt": debt,
            "total_liabilities": total_liabilities,
            "total_equity": equity,
            "capital_expenditures": r(p.da * 1.1),
            "debt_principal_repaid": p.principal_repaid,
        }
    return out


def money(v: int) -> str:
    return f"({abs(v):,})" if v < 0 else f"{v:,}"


def render_table(
    title: str, name: str, fields: list[str], fig: dict[int, dict[str, int]], years: tuple[int, ...]
) -> tuple[str, dict[str, int]]:
    lines = [
        f"# {title}",
        "",
        f"{name}",
        "Fiscal years ending December 31. Amounts in US dollars; parentheses are negative.",
        "",
        "| Line item | " + " | ".join(f"FY{y}" for y in years) + " |",
        "|---|" + "---|" * len(years),
    ]
    where: dict[str, int] = {}
    for f in fields:
        lines.append(f"| {LABELS[f]} | " + " | ".join(money(fig[y][f]) for y in years) + " |")
        where[f] = len(lines)
    return "\n".join(lines) + "\n", where


def ratio(n: D, d: D) -> str | None:
    return None if d == 0 else str((n / d).quantize(D("0.01"), rounding=ROUND_HALF_UP))


def expected_ratios(p: Params, fig: dict[int, dict[str, int]]) -> list[dict[str, object]]:
    """Independent reference implementation of the ratio definitions in docs."""
    out: list[dict[str, object]] = []
    for i, y in enumerate(p.years):
        f = {k: D(v) for k, v in fig[y].items()}
        rows = {
            "ebitda_margin": ratio(f["ebitda"], f["revenue"]),
            "current_ratio": ratio(f["total_current_assets"], f["total_current_liabilities"]),
            "leverage": ratio(f["total_debt"], f["ebitda"]),
            "interest_coverage": ratio(
                f["ebitda"] - f["depreciation_amortization"], f["interest_expense"]
            ),
            "debt_to_equity": ratio(f["total_debt"], f["total_equity"]),
            "dscr": ratio(f["ebitda"], f["interest_expense"] + f["debt_principal_repaid"]),
        }
        if i > 0:
            prev = D(fig[p.years[i - 1]]["revenue"])
            rows["revenue_growth"] = ratio(f["revenue"] - prev, prev)
        out += [{"name": k, "year": y, "value": v} for k, v in rows.items()]
    y = p.years[-1]
    f = {k: D(v) for k, v in fig[y].items()}
    monthly_rate = D(p.loan_rate) / 100 / 12
    n = p.amort * 12
    monthly = D(p.loan) * monthly_rate / (1 - (1 + monthly_rate) ** -n)
    ads = monthly.quantize(D("0.01"), rounding=ROUND_HALF_UP) * 12
    out += [
        {"name": "proposed_annual_debt_service", "year": y, "value": str(ads)},
        {
            "name": "pro_forma_dscr",
            "year": y,
            "value": ratio(f["ebitda"], f["interest_expense"] + f["debt_principal_repaid"] + ads),
        },
        {
            "name": "pro_forma_leverage",
            "year": y,
            "value": ratio(f["total_debt"] + D(p.loan), f["ebitda"]),
        },
    ]
    return out


def business_description(p: Params, name: str) -> str:
    top = p.top_customer_pct
    concentration = (
        f"Its largest customer accounted for approximately {top}% of FY{p.years[-1]} revenue "
        "under a contract that renews annually."
        if top >= 25
        else f"No single customer accounted for more than {top}% of FY{p.years[-1]} revenue."
    )
    return "\n".join(
        [
            "# Business description",
            "",
            f"{name} is a fictional {p.industry} business founded in {p.founded}, with "
            f"about {p.employees} employees.",
            "",
            f"The company serves commercial customers in its region. {concentration}",
            "",
            "Management has operated the business for more than five years. The owners "
            "guarantee existing bank debt personally.",
            "",
            "This document is synthetic and describes no real business.",
            "",
        ]
    )


def loan_application(p: Params, name: str) -> tuple[str, dict[str, int]]:
    lines = [
        "# Loan application",
        "",
        f"Applicant: {name}",
        "Product: commercial term loan",
        f"Requested amount: ${p.loan:,}",
        f"Interest rate: {p.loan_rate}% fixed",
        f"Term: {p.term} years",
        f"Amortization: {p.amort} years, level monthly payments",
        "Purpose: equipment purchase and working capital",
        "",
    ]
    where = {
        "requested_amount": 5,
        "interest_rate_pct": 6,
        "term_years": 7,
        "amortization_years": 8,
    }
    return "\n".join(lines), where


def build(p: Params) -> dict[str, object]:
    number = p.package_id.split("-")[1]
    name = f"Synthetic Borrower {number} ({p.industry})"
    fig = build_figures(p)
    folder = OUT / p.package_id
    (folder / "documents").mkdir(parents=True, exist_ok=True)
    docs: list[dict[str, str]] = []
    truth_years: dict[str, dict[str, dict[str, object]]] = {str(y): {} for y in p.years}

    statements = [
        (
            "income_statement",
            "Income statement",
            [
                "revenue",
                "cost_of_goods_sold",
                "operating_expenses",
                "ebitda",
                "depreciation_amortization",
                "interest_expense",
                "income_tax",
                "net_income",
            ],
        ),
        (
            "balance_sheet",
            "Balance sheet",
            [
                "cash",
                "accounts_receivable",
                "inventory",
                "total_current_assets",
                "total_assets",
                "total_current_liabilities",
                "total_debt",
                "total_liabilities",
                "total_equity",
            ],
        ),
        (
            "cash_flow",
            "Cash flow statement (selected lines)",
            [
                "capital_expenditures",
                "debt_principal_repaid",
            ],
        ),
    ]
    for kind, title, fields in statements:
        doc_id = f"{p.package_id}-{kind}"
        text, where = render_table(title, name, fields, fig, p.years)
        (folder / "documents" / f"{kind}.md").write_text(text)
        docs.append({"doc_id": doc_id, "kind": kind, "title": title, "file": f"{kind}.md"})
        for y in p.years:
            for f in fields:
                truth_years[str(y)][f] = {
                    "value": str(fig[y][f]),
                    "doc_id": doc_id,
                    "line": where[f],
                }

    loan_text, loan_where = loan_application(p, name)
    (folder / "documents" / "loan_application.md").write_text(loan_text)
    loan_doc = f"{p.package_id}-loan_application"
    docs.append(
        {
            "doc_id": loan_doc,
            "kind": "loan_application",
            "title": "Loan application",
            "file": "loan_application.md",
        }
    )
    loan_values = {
        "requested_amount": str(p.loan),
        "interest_rate_pct": p.loan_rate,
        "term_years": str(p.term),
        "amortization_years": str(p.amort),
    }
    truth_loan = {
        k: {"value": v, "doc_id": loan_doc, "line": loan_where[k]} for k, v in loan_values.items()
    }

    bd_text = business_description(p, name)
    (folder / "documents" / "business_description.md").write_text(bd_text)
    bd_line = next(i for i, t in enumerate(bd_text.splitlines(), 1) if "customer accounted" in t)
    truth_years[str(p.years[-1])]["largest_customer_share_pct"] = {
        "value": str(p.top_customer_pct),
        "doc_id": f"{p.package_id}-business_description",
        "line": bd_line,
    }
    docs.append(
        {
            "doc_id": f"{p.package_id}-business_description",
            "kind": "business_description",
            "title": "Business description",
            "file": "business_description.md",
        }
    )

    meta = {
        "package_id": p.package_id,
        "borrower_name": name,
        "industry": p.industry,
        "scenario": p.scenario,
        "documents": docs,
    }
    (folder / "package.json").write_text(json.dumps(meta, indent=2) + "\n")
    truth = {
        "package_id": p.package_id,
        "scenario": p.scenario,
        "years": truth_years,
        "loan": truth_loan,
        "expected_ratios": expected_ratios(p, fig),
    }
    (folder / "ground_truth.json").write_text(json.dumps(truth, indent=2) + "\n")
    return {
        "package_id": p.package_id,
        "borrower_name": name,
        "industry": p.industry,
        "scenario": p.scenario,
    }


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    catalog = [build(p) for p in PACKAGES]
    (OUT / "catalog.json").write_text(json.dumps(catalog, indent=2) + "\n")
    print(f"Wrote {len(catalog)} packages to {OUT}")


if __name__ == "__main__":
    main()
