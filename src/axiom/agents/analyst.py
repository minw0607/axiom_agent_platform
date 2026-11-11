"""Financial analyst: ratios and flags in code; the model writes only the narrative."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from axiom.agents.base import AgentContext, ModelCall, call_structured
from axiom.domain import BorrowerPackage, CheckResult, FinancialStatements, RatioResult, RiskFlag
from axiom.tools.policy import risk_flags
from axiom.tools.ratios import compute_ratios


class AnalystOutput(BaseModel):
    paragraphs: list[str] = Field(min_length=1, max_length=6)


def ratio_payload(ratios: list[RatioResult]) -> list[dict[str, Any]]:
    return [
        {
            "name": r.name,
            "year": r.year,
            "value": None if r.value is None else str(r.value),
            "note": r.note,
        }
        for r in ratios
    ]


def flag_payload(flags: list[RiskFlag]) -> list[dict[str, Any]]:
    return [f.model_dump(mode="json", exclude_none=True) for f in flags]


def check_payload(checks: list[CheckResult]) -> list[dict[str, Any]]:
    return [c.model_dump(mode="json", exclude_none=True) for c in checks if c.outcome != "passed"]


async def run_analyst(
    ctx: AgentContext,
    package: BorrowerPackage,
    fs: FinancialStatements,
    checks: list[CheckResult],
) -> tuple[list[RatioResult], list[RiskFlag], list[str], list[ModelCall]]:
    ratios = compute_ratios(fs)
    flags = risk_flags(fs, ratios, ctx.policy)
    prompt = ctx.prompt("analyst")
    out, calls = await call_structured(
        ctx,
        node="analyst",
        route="narrative",
        prompt=prompt,
        payload={
            "borrower": {"name": package.borrower_name, "industry": package.industry},
            "fiscal_years": fs.fiscal_years(),
            "ratios": ratio_payload(ratios),
            "risk_flags": flag_payload(flags),
            "reconciliation_issues": check_payload(checks),
        },
        schema=AnalystOutput,
        recording_key=f"analyst/{prompt.version}/{package.package_id}",
        validate=lambda o: [] if all(p.strip() for p in o.paragraphs) else ["empty paragraph"],
    )
    return ratios, flags, out.paragraphs, calls
