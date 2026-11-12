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
from dataclasses import dataclass, field, replace
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "synthetic" / "packages"
GOLDEN_REVIEW = ROOT / "evals" / "golden" / "v1" / "answers.md"
# Golden answers are a draft until a person has reviewed them (dev plan section 11).
GOLDEN_STATUS = "draft, awaiting review"
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
    omit: tuple[str, ...] = ()  # documents left out of the package


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

# Golden set additions (week 6): edge cases near each threshold. Loan amounts on the
# boundary cases were tuned so the rounded ratio lands exactly where the case says.
BASE = PACKAGES[0]  # pkg-01, a healthy machining business
PACKAGES += [
    replace(
        BASE,
        package_id="pkg-11",
        industry="dental laboratory",
        scenario="dscr_at_minimum",
        revenue0=7_200_000,
        loan=1_480_000,
        loan_rate="8.00",
        term=5,
        amort=5,
        employees=45,
    ),
    replace(
        BASE,
        package_id="pkg-12",
        industry="sign manufacturing",
        scenario="dscr_just_below_minimum",
        revenue0=7_200_000,
        loan=1_520_000,
        loan_rate="8.00",
        term=5,
        amort=5,
        employees=45,
    ),
    replace(
        BASE,
        package_id="pkg-13",
        industry="industrial cleaning",
        scenario="leverage_at_maximum",
        revenue0=9_000_000,
        cogs_pct=0.55,
        opex_pct=0.27,
        loan=3_910_000,
        loan_rate="7.00",
        term=10,
        amort=10,
        debt=3_000_000,
        principal_repaid=300_000,
        employees=90,
    ),
    replace(
        BASE,
        package_id="pkg-14",
        industry="metal fabrication",
        scenario="multiple_breaches",
        revenue0=10_500_000,
        growth=(0.02, 0.01),
        cogs_pct=0.66,
        opex_pct=0.24,
        debt=4_200_000,
        debt_rate=0.09,
        principal_repaid=350_000,
        cash_pct=0.02,
        ar_pct=0.07,
        inv_pct=0.04,
        other_ca_pct=0.01,
        cl_pct=0.16,
        loan=1_800_000,
        loan_rate="9.00",
        term=7,
        amort=7,
        employees=70,
    ),
    replace(
        BASE,
        package_id="pkg-15",
        industry="event staffing",
        scenario="negative_ebitda",
        revenue0=5_400_000,
        growth=(-0.04, -0.06),
        cogs_pct=0.71,
        opex_pct=0.32,
        debt=900_000,
        principal_repaid=150_000,
        fixed_assets=900_000,
        loan=500_000,
        loan_rate="9.50",
        term=5,
        amort=5,
        employees=150,
    ),
    replace(
        BASE,
        package_id="pkg-16",
        industry="solar installation",
        scenario="growth_with_oversized_loan",
        revenue0=6_000_000,
        growth=(0.22, 0.25),
        cogs_pct=0.64,
        opex_pct=0.25,
        debt=1_000_000,
        principal_repaid=200_000,
        loan=4_000_000,
        loan_rate="8.75",
        term=5,
        amort=5,
        founded=2017,
        employees=55,
    ),
    replace(
        BASE,
        package_id="pkg-17",
        industry="medical billing",
        scenario="concentration_disclosure",
        revenue0=8_400_000,
        cogs_pct=0.48,
        opex_pct=0.36,
        loan=1_000_000,
        loan_rate="7.50",
        term=5,
        amort=5,
        top_customer_pct=32,
        employees=95,
    ),
    replace(
        BASE,
        package_id="pkg-18",
        industry="aerospace machining",
        scenario="concentration_at_maximum",
        revenue0=16_000_000,
        loan=2_000_000,
        top_customer_pct=40,
        employees=130,
    ),
    replace(
        BASE,
        package_id="pkg-19",
        industry="office furniture",
        scenario="revenue_decline_at_trigger",
        revenue0=12_000_000,
        growth=(0.03, -0.10),
        cogs_pct=0.60,
        opex_pct=0.24,
        loan=900_000,
        loan_rate="7.75",
        term=5,
        amort=5,
        employees=80,
    ),
    replace(
        BASE,
        package_id="pkg-20",
        industry="specialty foods",
        scenario="two_warnings",
        revenue0=14_000_000,
        growth=(0.01, -0.12),
        cogs_pct=0.60,
        opex_pct=0.22,
        loan=800_000,
        loan_rate="7.75",
        term=5,
        amort=5,
        top_customer_pct=29,
        employees=100,
    ),
    replace(
        BASE,
        package_id="pkg-21",
        industry="plumbing supply",
        scenario="missing_cash_flow",
        revenue0=11_000_000,
        loan=1_200_000,
        omit=("cash_flow",),
        employees=65,
    ),
    replace(
        BASE,
        package_id="pkg-22",
        industry="auto body repair",
        scenario="small_imbalance",
        revenue0=6_300_000,
        loan=700_000,
        loan_rate="8.00",
        term=5,
        amort=5,
        imbalance={2025: 3},
        employees=38,
    ),
    replace(
        BASE,
        package_id="pkg-23",
        industry="tutoring services",
        scenario="loan_below_minimum",
        revenue0=3_100_000,
        cogs_pct=0.45,
        opex_pct=0.38,
        debt=400_000,
        principal_repaid=80_000,
        fixed_assets=600_000,
        loan=200_000,
        loan_rate="8.50",
        term=3,
        amort=3,
        employees=30,
    ),
    replace(
        BASE,
        package_id="pkg-24",
        industry="hotel operation",
        scenario="low_interest_coverage",
        revenue0=9_800_000,
        cogs_pct=0.50,
        opex_pct=0.34,
        da=1_100_000,
        debt=6_000_000,
        debt_rate=0.095,
        principal_repaid=150_000,
        fixed_assets=12_000_000,
        loan=600_000,
        loan_rate="9.00",
        term=10,
        amort=20,
        employees=120,
    ),
    replace(
        BASE,
        package_id="pkg-25",
        industry="engineering consulting",
        scenario="healthy_large_loan",
        revenue0=42_000_000,
        growth=(0.06, 0.07),
        cogs_pct=0.55,
        opex_pct=0.30,
        debt=6_000_000,
        principal_repaid=1_000_000,
        fixed_assets=6_000_000,
        loan=5_000_000,
        loan_rate="7.00",
        term=7,
        amort=7,
        employees=260,
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
    no_principal = "cash_flow" in p.omit  # debt service cannot be computed without it
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
            "dscr": None
            if no_principal
            else ratio(f["ebitda"], f["interest_expense"] + f["debt_principal_repaid"]),
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
            "value": None
            if no_principal
            else ratio(f["ebitda"], f["interest_expense"] + f["debt_principal_repaid"] + ads),
        },
        {
            "name": "pro_forma_leverage",
            "year": y,
            "value": ratio(f["total_debt"] + D(p.loan), f["ebitda"]),
        },
    ]
    return out


def thresholds() -> dict[str, D]:
    """Threshold values from the policy file; the flag logic below is written separately."""
    import yaml

    raw = yaml.safe_load((ROOT / "config" / "policy_thresholds.yaml").read_text())
    return {k: D(str(v["value"])) for k, v in raw["thresholds"].items()}


def golden(
    p: Params, fig: dict[int, dict[str, int]], ratios: list[dict[str, object]]
) -> dict[str, object]:
    """Expected risk flags, acceptable draft recommendations and key findings, from the
    credit policy text (data/synthetic/credit_policy/credit_policy.md)."""
    t = thresholds()
    last = p.years[-1]
    val = {(str(x["name"]), int(x["year"])): x["value"] for x in ratios}  # type: ignore[call-overload]

    def num(name: str, year: int = last) -> D | None:
        v = val.get((name, year))
        return None if v is None else D(str(v))

    flags: list[dict[str, object]] = []
    findings: list[dict[str, object]] = []

    def flag(
        code: str, severity: str, text: str, terms: list[str], year: int | None = last
    ) -> None:
        flags.append({"code": code, "severity": severity, "year": year})
        findings.append({"id": f"KF{len(findings) + 1}", "text": text, "terms": terms})

    pf_dscr = num("pro_forma_dscr")
    if pf_dscr is None:
        flag(
            "DSCR_BELOW_MIN_UNDEFINED",
            "high",
            "Pro forma DSCR cannot be computed because debt repayment is not reported.",
            ["DSCR"],
        )
    else:
        if pf_dscr < t["min_pro_forma_dscr"]:
            flag(
                "DSCR_BELOW_MIN",
                "high",
                f"Pro forma DSCR of {pf_dscr} is below the policy minimum of 1.25 (CP-4.1).",
                ["DSCR", str(pf_dscr)],
            )
        if pf_dscr < t["dscr_decline_floor"]:
            flag(
                "DSCR_BELOW_DECLINE_FLOOR",
                "high",
                f"Pro forma DSCR of {pf_dscr} is below the 1.00 decline floor (CP-6.3), so the "
                "request is not eligible for an exception.",
                ["DSCR", str(pf_dscr)],
            )
    cr = num("current_ratio")
    if cr is not None and cr < t["min_current_ratio"]:
        flag(
            "LIQUIDITY_BELOW_MIN",
            "high",
            f"Current ratio of {cr} is below the policy minimum of 1.20 (CP-4.3).",
            ["current ratio", str(cr)],
        )
    ic = num("interest_coverage")
    if ic is not None and ic < t["min_interest_coverage"]:
        flag(
            "COVERAGE_BELOW_MIN",
            "high",
            f"Interest coverage of {ic} is below the policy minimum of 2.00 (CP-4.4).",
            ["coverage", str(ic)],
        )
    lev = num("pro_forma_leverage")
    if fig[last]["ebitda"] <= 0:
        flag(
            "NEGATIVE_EBITDA",
            "high",
            f"FY{last} EBITDA is negative ({fig[last]['ebitda']}), so leverage is not "
            "meaningful and cash flow does not cover debt service.",
            ["EBITDA", str(fig[last]["ebitda"])],
        )
    elif lev is not None and lev > t["max_pro_forma_leverage"]:
        flag(
            "LEVERAGE_ABOVE_MAX",
            "high",
            f"Pro forma leverage of {lev}x exceeds the policy maximum of 3.50x (CP-4.2).",
            ["leverage", str(lev)],
        )
    for y in p.years[1:]:
        g = num("revenue_growth", y)
        if g is not None and g < t["max_revenue_decline"]:
            flag(
                "REVENUE_DECLINE",
                "warning",
                f"Revenue changed by {g} in FY{y}, beyond the policy trigger of -0.10 (CP-4.5).",
                ["revenue", str(g)],
                y,
            )
    share = D(p.top_customer_pct)
    if share > t["max_customer_concentration_pct"]:
        flag(
            "CONCENTRATION_ABOVE_MAX",
            "high",
            f"The largest customer is {share}% of revenue, above the 40% maximum (CP-5.2); a "
            "sensitivity analysis is required.",
            ["customer", f"{share}%"],
        )
    elif share > t["concentration_disclosure_pct"]:
        flag(
            "CONCENTRATION_DISCLOSURE",
            "warning",
            f"The largest customer is {share}% of revenue, above 25% (CP-5.1); the memo must "
            "identify the contract terms.",
            ["customer", f"{share}%"],
        )
    if D(len(p.years)) < t["min_history_years"]:
        flag(
            "INSUFFICIENT_HISTORY",
            "warning",
            f"{len(p.years)} fiscal years are provided; the policy requires 3 (CP-3.1).",
            ["fiscal years"],
            None,
        )

    # A value exactly on a limit meets it; a good memo says so rather than calling it a breach.
    at_limit = [
        ("Pro forma DSCR", pf_dscr, t["min_pro_forma_dscr"], "minimum", "CP-4.1", "DSCR"),
        ("Pro forma leverage", lev, t["max_pro_forma_leverage"], "maximum", "CP-4.2", "leverage"),
        ("Current ratio", cr, t["min_current_ratio"], "minimum", "CP-4.3", "current ratio"),
        ("Interest coverage", ic, t["min_interest_coverage"], "minimum", "CP-4.4", "coverage"),
        (
            "FY" + str(last) + " revenue growth",
            num("revenue_growth"),
            t["max_revenue_decline"],
            "trigger",
            "CP-4.5",
            "revenue",
        ),
        (
            "The largest customer's share of revenue, in percent,",
            share,
            t["max_customer_concentration_pct"],
            "maximum",
            "CP-5.2",
            "customer",
        ),
    ]
    for label, value, limit, kind, ref, term in at_limit:
        if value is not None and value == limit:
            findings.append(
                {
                    "id": f"KF{len(findings) + 1}",
                    "text": f"{label} of {value} is exactly at the policy {kind} ({ref}); "
                    "it meets the standard and is not a breach.",
                    "terms": [term, str(value)],
                }
            )

    refer_reasons = []
    if p.imbalance:
        y, gap = next(iter(p.imbalance.items()))
        refer_reasons.append(
            (
                f"FY{y} total assets exceed total liabilities plus equity by {gap}, so the "
                "balance sheet does not balance.",
                ["balance"],
            )
        )
    if p.omit:
        refer_reasons.append(
            (
                f"Required document missing: {', '.join(k.replace('_', ' ') for k in p.omit)} "
                "statement (CP-3.1); the policy does not say what follows, so refer is a "
                "reading to confirm.",
                ["cash flow"],
            )
        )
    if not t["min_loan_amount"] <= D(p.loan) <= t["max_loan_amount"]:
        refer_reasons.append(
            (
                f"The requested {p.loan} is outside the policy's scope of 250,000 to 5,000,000 "
                "(CP-1.1).",
                [str(p.loan)],
            )
        )
    for reason, terms in refer_reasons:
        findings.append({"id": f"KF{len(findings) + 1}", "text": reason, "terms": terms})

    high = [f for f in flags if f["severity"] == "high"]
    if refer_reasons:
        acceptable = ["refer"]
    elif any(f["code"] == "DSCR_BELOW_DECLINE_FLOOR" for f in flags) or any(
        f["code"] == "NEGATIVE_EBITDA" for f in flags
    ):
        acceptable = ["decline"]
    elif high:
        acceptable = ["approve_with_conditions", "decline"]
    elif flags:
        acceptable = ["approve_with_conditions"]
    else:
        acceptable = ["approve"]
    if not findings:
        findings.append(
            {
                "id": "KF1",
                "text": f"Pro forma DSCR of {pf_dscr} meets the 1.25 minimum and pro forma "
                f"leverage of {lev}x is within 3.50x; no policy threshold is breached.",
                "terms": ["DSCR", str(pf_dscr)],
            }
        )
    return {
        "status": GOLDEN_STATUS,
        "expected_flags": flags,
        "acceptable_recommendations": acceptable,
        "key_findings": findings,
    }


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
        if kind in p.omit:
            continue
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
        "golden": golden(p, fig, expected_ratios(p, fig)),
    }
    (folder / "ground_truth.json").write_text(json.dumps(truth, indent=2) + "\n")
    return {
        "package_id": p.package_id,
        "borrower_name": name,
        "industry": p.industry,
        "scenario": p.scenario,
        "golden": truth["golden"],
    }


def review_page(built: list[dict[str, object]]) -> str:
    """The golden answers in one readable page, for the person who reviews them."""
    lines = [
        "# Golden set v1: expected answers for review",
        "",
        f"Status: **{GOLDEN_STATUS}**. Generated by `scripts/generate_synthetic.py`; edit the",
        "generator, not this page. Figures and ratios are computed by script. The flags, the",
        "acceptable draft recommendations and the key findings below are read from the credit",
        "policy and need a person's review: is each finding right, and is anything missing?",
        'The policy does not define "refer"; using it for a missing document, an out-of-scope',
        "loan or a balance sheet that does not balance is a reading for the reviewer to confirm.",
        "",
    ]
    for b in built:
        g = b["golden"]
        assert isinstance(g, dict)
        flags = ", ".join(f"{f['code']} ({f['severity']})" for f in g["expected_flags"]) or "none"
        lines += [
            f"## {b['package_id']}: {b['industry']} ({b['scenario'].replace('_', ' ')})",  # type: ignore[union-attr]
            "",
            f"- **Expected risk flags:** {flags}",
            "- **Acceptable draft recommendation:** "
            + " or ".join(r.replace("_", " ") for r in g["acceptable_recommendations"]),
            "- **Key findings a good memo states:**",
            *[f"  - {k['id']}: {k['text']}" for k in g["key_findings"]],
            "",
        ]
    return "\n".join(lines)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    built = [build(p) for p in PACKAGES]
    catalog = [{k: v for k, v in b.items() if k != "golden"} for b in built]
    (OUT / "catalog.json").write_text(json.dumps(catalog, indent=2) + "\n")
    GOLDEN_REVIEW.parent.mkdir(parents=True, exist_ok=True)
    GOLDEN_REVIEW.write_text(review_page(built))
    print(f"Wrote {len(catalog)} packages to {OUT}")


if __name__ == "__main__":
    main()
