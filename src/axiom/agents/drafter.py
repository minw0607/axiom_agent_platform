"""Memo drafter: a fixed-template memo in which every factual claim is cited.

Code checks the draft before it is accepted: the template headings, a citation on every
factual claim, citations that point at a real line or a retrieved chunk, and a draft
recommendation consistent with the deterministic findings.
"""

from __future__ import annotations

from typing import Any

from axiom.agents.analyst import check_payload, flag_payload, ratio_payload
from axiom.agents.base import AgentContext, ModelCall, call_structured
from axiom.domain import (
    BorrowerPackage,
    CheckResult,
    FinancialStatements,
    MemoDraft,
    RatioResult,
    ReviewDecision,
    RiskFlag,
)
from axiom.mcp_clients.evidence import EvidenceHit

HEADINGS = ("Borrower and request", "Financial analysis", "Risk factors", "Draft recommendation")
EVIDENCE_QUERY = "business description customers concentration ownership operating history"
KEY_FIELDS = (
    "revenue",
    "ebitda",
    "interest_expense",
    "net_income",
    "total_current_assets",
    "total_current_liabilities",
    "total_assets",
    "total_liabilities",
    "total_debt",
    "total_equity",
)
FINAL_DECISION_PHRASES = ("loan is approved", "is hereby approved", "loan is declined")


def figure_sources(fs: FinancialStatements) -> list[dict[str, Any]]:
    latest = fs.fiscal_years()[-1]
    out = []
    for field in KEY_FIELDS:
        fig = fs.years[latest].get(field)
        if fig is not None:
            out.append(
                {
                    "field": field,
                    "year": latest,
                    "value": str(fig.value),
                    "doc_id": fig.source.doc_id,
                    "line": fig.source.line,
                }
            )
    for field, fig in fs.loan.model_dump().items():
        out.append(
            {
                "field": field,
                "year": None,
                "value": str(fig["value"]),
                "doc_id": fig["source"]["doc_id"],
                "line": fig["source"]["line"],
            }
        )
    return out


def validate_draft(
    draft: MemoDraft,
    package: BorrowerPackage,
    chunk_ids: set[str],
    flags: list[RiskFlag],
    checks: list[CheckResult],
) -> list[str]:
    errors: list[str] = []
    headings = tuple(s.heading for s in draft.sections)
    if headings != HEADINGS:
        errors.append(f"sections must be exactly {list(HEADINGS)}, got {list(headings)}")
    line_counts = {d.doc_id: len(d.text.splitlines()) for d in package.documents}
    for section in draft.sections:
        if not section.claims:
            errors.append(f"section {section.heading!r} has no claims")
        for claim in section.claims:
            lowered = claim.text.lower()
            if any(p in lowered for p in FINAL_DECISION_PHRASES):
                errors.append(f"final-decision language in: {claim.text[:60]!r}")
            if section.heading != "Draft recommendation" and not claim.citations:
                errors.append(f"uncited claim in {section.heading!r}: {claim.text[:60]!r}")
            for c in claim.citations:
                if c.doc_id not in line_counts:
                    errors.append(f"citation to unknown document {c.doc_id!r}")
                elif c.locator.startswith("line "):
                    n = c.locator.removeprefix("line ")
                    if not n.isdigit() or not 1 <= int(n) <= line_counts[c.doc_id]:
                        errors.append(f"citation to a missing line: {c.doc_id} {c.locator}")
                elif c.locator not in chunk_ids:
                    errors.append(f"citation to an unretrieved chunk: {c.locator!r}")
    if any(c.outcome == "triggered" for c in checks) and draft.draft_recommendation != "refer":
        errors.append("reconciliation failed, so the draft recommendation must be 'refer'")
    elif (
        any(f.code == "DSCR_BELOW_DECLINE_FLOOR" for f in flags)
        and draft.draft_recommendation != "decline"
    ):
        errors.append("pro forma DSCR is below the decline floor, so the draft must be 'decline'")
    elif flags and draft.draft_recommendation == "approve":
        errors.append("a risk flag is raised, so the draft cannot be 'approve'")
    return errors


async def run_drafter(
    ctx: AgentContext,
    job_id: str,
    package: BorrowerPackage,
    fs: FinancialStatements,
    ratios: list[RatioResult],
    flags: list[RiskFlag],
    checks: list[CheckResult],
    narrative: list[str],
    revision: tuple[MemoDraft, list[ReviewDecision]] | None = None,
    fix: tuple[MemoDraft, list[dict[str, str]]] | None = None,
) -> tuple[MemoDraft, list[EvidenceHit], list[ModelCall]]:
    """Draft the memo, or with `revision` (the previous draft and the reviews so far)
    revise it for the credit officer's comments under the same validation. With `fix`
    (the draft and the problems the guardrails found), correct it once (automated fix)."""
    hits = await ctx.evidence.retrieve(job_id, EVIDENCE_QUERY, k=4, collection="job")
    chunk_ids = {h.chunk_id for h in hits}
    payload: dict[str, Any] = {
        "borrower": {"name": package.borrower_name, "industry": package.industry},
        "figure_sources": figure_sources(fs),
        "ratios": ratio_payload(ratios),
        "risk_flags": flag_payload(flags),
        "reconciliation_issues": check_payload(checks),
        "analyst_narrative": narrative,
        "evidence": [{"doc_id": h.doc_id, "chunk_id": h.chunk_id, "text": h.text} for h in hits],
    }
    prompt_id, key_suffix = "drafter", ""
    if revision is not None:
        previous, reviews = revision
        rounds = [r for r in reviews if r.decision == "revise"]
        payload["previous_draft"] = previous.model_dump(mode="json")
        payload["reviewer_comments"] = [r.comments for r in rounds]
        prompt_id, key_suffix = "drafter_revision", f".r{len(rounds)}"
    if fix is not None:
        failed, problems = fix
        payload["previous_draft"] = failed.model_dump(mode="json")
        payload["problems"] = problems
        prompt_id = "drafter_fix"
    prompt = ctx.prompt(prompt_id)
    draft, calls = await call_structured(
        ctx,
        node="drafter",
        route="narrative",
        prompt=prompt,
        payload=payload,
        schema=MemoDraft,
        recording_key=f"{prompt_id}/{prompt.version}/{package.package_id}{key_suffix}",
        validate=lambda d: validate_draft(d, package, chunk_ids, flags, checks),
    )
    return draft, hits, calls
