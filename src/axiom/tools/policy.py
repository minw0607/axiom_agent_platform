"""Credit policy thresholds and the risk flags derived from them (deterministic)."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

import yaml

from axiom.domain import FinancialStatements, RatioResult, RiskFlag, Severity
from axiom.settings import Settings


@dataclass(frozen=True)
class Threshold:
    value: Decimal
    policy_ref: str


@dataclass(frozen=True)
class PolicyThresholds:
    version: str
    thresholds: dict[str, Threshold]

    def __getitem__(self, name: str) -> Threshold:
        return self.thresholds[name]


def load_policy(path: Path | None = None) -> PolicyThresholds:
    path = path or Settings.from_env().config_dir / "policy_thresholds.yaml"
    raw = yaml.safe_load(path.read_text())
    return PolicyThresholds(
        version=str(raw["policy_version"]),
        thresholds={
            name: Threshold(Decimal(str(t["value"])), str(t["policy_ref"]))
            for name, t in raw["thresholds"].items()
        },
    )


def _find(ratios: list[RatioResult], name: str, year: int) -> RatioResult | None:
    return next((r for r in ratios if r.name == name and r.year == year), None)


def risk_flags(
    fs: FinancialStatements, ratios: list[RatioResult], policy: PolicyThresholds
) -> list[RiskFlag]:
    """Compare ratios with policy thresholds. Undefined ratios are flagged, never passed."""
    flags: list[RiskFlag] = []
    years = fs.fiscal_years()
    latest = years[-1]

    def check_min(ratio: str, year: int, key: str, code: str, label: str) -> None:
        t = policy[key]
        r = _find(ratios, ratio, year)
        if r is None or r.value is None:
            flags.append(
                RiskFlag(
                    code=f"{code}_UNDEFINED",
                    severity=Severity.HIGH,
                    year=year,
                    ratio=ratio,
                    policy_ref=t.policy_ref,
                    message=f"{label} could not be computed ({r.note if r else 'missing'}).",
                )
            )
        elif r.value < t.value:
            flags.append(
                RiskFlag(
                    code=code,
                    severity=Severity.HIGH,
                    year=year,
                    ratio=ratio,
                    policy_ref=t.policy_ref,
                    message=f"{label} {r.value} is below the policy minimum of {t.value}.",
                )
            )

    check_min("pro_forma_dscr", latest, "min_pro_forma_dscr", "DSCR_BELOW_MIN", "Pro forma DSCR")
    check_min("current_ratio", latest, "min_current_ratio", "LIQUIDITY_BELOW_MIN", "Current ratio")
    floor_t = policy["dscr_decline_floor"]
    dscr = _find(ratios, "pro_forma_dscr", latest)
    if dscr is not None and dscr.value is not None and dscr.value < floor_t.value:
        flags.append(
            RiskFlag(
                code="DSCR_BELOW_DECLINE_FLOOR",
                severity=Severity.HIGH,
                year=latest,
                ratio="pro_forma_dscr",
                policy_ref=floor_t.policy_ref,
                message=f"Pro forma DSCR {dscr.value} is below {floor_t.value}: cash flow does "
                "not cover debt service, so the request is not eligible for an exception and "
                "the draft recommendation is decline.",
            )
        )
    check_min(
        "interest_coverage",
        latest,
        "min_interest_coverage",
        "COVERAGE_BELOW_MIN",
        "Interest coverage",
    )

    lev_t = policy["max_pro_forma_leverage"]
    lev = _find(ratios, "pro_forma_leverage", latest)
    ebitda = fs.get(latest, "ebitda")
    if ebitda is not None and ebitda <= 0:
        flags.append(
            RiskFlag(
                code="NEGATIVE_EBITDA",
                severity=Severity.HIGH,
                year=latest,
                ratio="ebitda",
                policy_ref=lev_t.policy_ref,
                message="EBITDA is zero or negative, so leverage is not meaningful.",
            )
        )
    elif lev is None or lev.value is None:
        flags.append(
            RiskFlag(
                code="LEVERAGE_ABOVE_MAX_UNDEFINED",
                severity=Severity.HIGH,
                year=latest,
                ratio="pro_forma_leverage",
                policy_ref=lev_t.policy_ref,
                message="Pro forma leverage could not be computed.",
            )
        )
    elif lev.value > lev_t.value:
        flags.append(
            RiskFlag(
                code="LEVERAGE_ABOVE_MAX",
                severity=Severity.HIGH,
                year=latest,
                ratio="pro_forma_leverage",
                policy_ref=lev_t.policy_ref,
                message=f"Pro forma leverage {lev.value}x exceeds the policy maximum of "
                f"{lev_t.value}x.",
            )
        )

    decline_t = policy["max_revenue_decline"]
    for year in years[1:]:
        growth = _find(ratios, "revenue_growth", year)
        if growth is not None and growth.value is not None and growth.value < decline_t.value:
            flags.append(
                RiskFlag(
                    code="REVENUE_DECLINE",
                    severity=Severity.WARNING,
                    year=year,
                    ratio="revenue_growth",
                    policy_ref=decline_t.policy_ref,
                    message=f"Revenue changed by {growth.value} in {year}, beyond the "
                    f"policy trigger of {decline_t.value}.",
                )
            )

    max_conc_t = policy["max_customer_concentration_pct"]
    disclose_t = policy["concentration_disclosure_pct"]
    share = fs.get(latest, "largest_customer_share_pct")
    if share is None:
        flags.append(
            RiskFlag(
                code="CONCENTRATION_UNDEFINED",
                severity=Severity.HIGH,
                year=latest,
                ratio="largest_customer_share_pct",
                policy_ref=max_conc_t.policy_ref,
                message="The largest customer's share of revenue was not found, so "
                "concentration could not be checked.",
            )
        )
    elif share > max_conc_t.value:
        flags.append(
            RiskFlag(
                code="CONCENTRATION_ABOVE_MAX",
                severity=Severity.HIGH,
                year=latest,
                ratio="largest_customer_share_pct",
                policy_ref=max_conc_t.policy_ref,
                message=f"The largest customer is {share}% of revenue, above "
                f"{max_conc_t.value}%: a sensitivity analysis is required and approval is "
                "normally only with conditions.",
            )
        )
    elif share > disclose_t.value:
        flags.append(
            RiskFlag(
                code="CONCENTRATION_DISCLOSURE",
                severity=Severity.WARNING,
                year=latest,
                ratio="largest_customer_share_pct",
                policy_ref=disclose_t.policy_ref,
                message=f"The largest customer is {share}% of revenue, above "
                f"{disclose_t.value}%: the memo must identify the customer's contract terms.",
            )
        )

    history_t = policy["min_history_years"]
    if Decimal(len(years)) < history_t.value:
        flags.append(
            RiskFlag(
                code="INSUFFICIENT_HISTORY",
                severity=Severity.WARNING,
                policy_ref=history_t.policy_ref,
                message=f"{len(years)} fiscal years provided; the policy requires "
                f"{history_t.value}.",
            )
        )
    return flags
