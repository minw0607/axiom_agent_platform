"""What the platform's tested tools produced for a package, as plain lines for the grader.

The drafter is given computed ratios, risk flags and reconciliation results alongside the
documents, and its claims about them cite the figures they rest on. A document line cannot
show "pro forma DSCR 0.93" or "no risk flags were raised", so the grader gets these outputs
too and judges such claims against them. Number provenance already checks the numbers
exactly; this lets citation support judge the words.
"""

from __future__ import annotations

from axiom.domain import CheckResult, RatioResult, RiskFlag
from axiom.tools.policy import PolicyThresholds


def platform_outputs(
    ratios: list[RatioResult],
    flags: list[RiskFlag],
    checks: list[CheckResult],
    policy: PolicyThresholds,
) -> tuple[str, ...]:
    lines: list[str] = []
    for year in sorted({r.year for r in ratios}):
        shown = [
            f"{r.name} {'undefined' if r.value is None else r.value}"
            for r in ratios
            if r.year == year
        ]
        lines.append(f"Ratios computed by the platform for FY{year}: " + "; ".join(shown) + ".")
    if flags:
        for f in flags:
            when = f" (FY{f.year})" if f.year else ""
            lines.append(f"Risk flag {f.code}, {f.severity.value} severity{when}: {f.message}")
    else:
        lines.append("Risk flags raised by the platform: none.")
    failed = [c for c in checks if c.outcome != "passed"]
    if failed:
        for c in failed:
            when = f" FY{c.year}" if c.year else ""
            lines.append(f"Reconciliation check {c.name}{when} {c.outcome}: {c.detail}")
    else:
        lines.append("Reconciliation checks: all passed; none failed.")
    lines.append(
        f"Credit policy {policy.version} thresholds: "
        + "; ".join(f"{k} {t.value} ({t.policy_ref})" for k, t in policy.thresholds.items())
        + "."
    )
    return tuple(lines)
