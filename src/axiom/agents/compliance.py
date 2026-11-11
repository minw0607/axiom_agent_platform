"""Compliance reviewer: reads a draft memo against the cited lending references.

The rule engine checks what code can match. This agent covers what needs reading: whether
a claim relies on a prohibited basis, whether the analysis covers repayment capacity, and,
for a draft decline, whether the reasons are specific. Code chooses the topics, retrieves
the reference passages and sets each finding's severity; the model only judges each topic
and cites the passages it relied on.

Fail closed: when retrieval finds no passages, or the model can't produce a valid review,
every topic is recorded as not_evaluated and the job continues to human review.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field

from axiom.agents.base import AgentContext, AgentOutputError, ModelCall, call_structured
from axiom.domain import BorrowerPackage, Citation, ComplianceFinding, MemoDraft, Severity
from axiom.mcp_clients.evidence import EvidenceHit
from axiom.router.types import FailClosed, ProviderError


@dataclass(frozen=True)
class Topic:
    rule_id: str
    reference: str
    question: str
    query: str
    severity: Severity


TOPICS = (
    Topic(
        rule_id="REF_PROHIBITED_BASIS",
        reference="12 CFR 1002.4(a), 1002.6(b)",
        question=(
            "Does any claim mention, rely on, or hint at a prohibited basis (race, color, "
            "religion, national origin, sex, marital status, age, receipt of public "
            "assistance, or exercise of consumer credit rights) of the applicant, its owners "
            "or guarantors? Triggered if yes."
        ),
        query="prohibited basis discriminate race color religion national origin sex marital "
        "status age public assistance evaluating creditworthiness",
        severity=Severity.HIGH,
    ),
    Topic(
        rule_id="REF_REPAYMENT_CAPACITY",
        reference="OCC Rating Credit Risk, Financial Statement Analysis",
        question=(
            "Does the draft analyze the borrower's capacity to repay from business cash flow, "
            "including debt service coverage, and the balance sheet, including leverage and "
            "liquidity? Triggered if any of these is missing."
        ),
        query="ability to repay cash flow debt service coverage balance sheet leverage "
        "liquidity financial statement analysis",
        severity=Severity.WARNING,
    ),
    Topic(
        rule_id="REF_ADVERSE_ACTION_REASONS",
        reference="12 CFR 1002.9(a)(3)(ii), 1002.9(b)(2)",
        question=(
            "The draft recommends decline. Do its claims give specific principal reasons that "
            "could support a statement of specific reasons, rather than only saying the "
            "request falls outside internal standards or policy? Triggered if not."
        ),
        query="adverse action statement of specific reasons principal reasons business credit "
        "notify applicant",
        severity=Severity.WARNING,
    ),
)
TOPIC_BY_ID = {t.rule_id: t for t in TOPICS}
PASSAGES_PER_TOPIC = 3


class TopicAssessment(BaseModel):
    rule_id: str
    outcome: Literal["passed", "triggered"]
    message: str = Field(max_length=400)
    claim_ids: list[str] = Field(description="Ids of the draft claims the assessment is about")
    reference_chunk_ids: list[str] = Field(description="Reference passages relied on")


class ComplianceReview(BaseModel):
    assessments: list[TopicAssessment]


def topics_for(draft: MemoDraft) -> tuple[Topic, ...]:
    """Adverse-action reasons apply only to a draft decline; the other topics always apply."""
    return tuple(
        t
        for t in TOPICS
        if t.rule_id != "REF_ADVERSE_ACTION_REASONS" or draft.draft_recommendation == "decline"
    )


def claim_index(draft: MemoDraft) -> dict[str, str]:
    return {
        f"s{i}.c{j}": claim.text
        for i, section in enumerate(draft.sections, 1)
        for j, claim in enumerate(section.claims, 1)
    }


def validate_review(
    review: ComplianceReview,
    topics: tuple[Topic, ...],
    claim_ids: set[str],
    chunk_ids: set[str],
) -> list[str]:
    errors: list[str] = []
    got = [a.rule_id for a in review.assessments]
    wanted = [t.rule_id for t in topics]
    if sorted(got) != sorted(wanted):
        errors.append(f"assess exactly these topics once each: {wanted}, got {got}")
    for a in review.assessments:
        if not a.reference_chunk_ids:
            errors.append(f"{a.rule_id}: cite at least one reference passage")
        for cid in a.reference_chunk_ids:
            if cid not in chunk_ids:
                errors.append(f"{a.rule_id}: {cid!r} is not a retrieved reference passage")
        for cid in a.claim_ids:
            if cid not in claim_ids:
                errors.append(f"{a.rule_id}: {cid!r} is not a claim id in the draft")
        if a.outcome == "triggered" and a.rule_id == "REF_PROHIBITED_BASIS" and not a.claim_ids:
            errors.append("REF_PROHIBITED_BASIS triggered: name the claim ids concerned")
    return errors


def not_evaluated(topics: tuple[Topic, ...], reason: str) -> list[ComplianceFinding]:
    return [
        ComplianceFinding(
            rule_id=t.rule_id,
            policy_ref=t.reference,
            outcome="not_evaluated",
            severity=Severity.INFO,
            message=f"Not evaluated: {reason}.",
            source="compliance_reviewer",
        )
        for t in topics
    ]


async def _retrieve(ctx: AgentContext, job_id: str, topics: tuple[Topic, ...]) -> list[EvidenceHit]:
    seen: dict[str, EvidenceHit] = {}
    for t in topics:
        for hit in await ctx.evidence.retrieve(
            job_id, t.query, k=PASSAGES_PER_TOPIC, collection="lending_refs"
        ):
            seen.setdefault(hit.chunk_id, hit)
    return list(seen.values())


async def run_compliance_review(
    ctx: AgentContext, job_id: str, package: BorrowerPackage, draft: MemoDraft, revision: int = 0
) -> tuple[list[ComplianceFinding], list[EvidenceHit], list[ModelCall]]:
    topics = topics_for(draft)
    hits = await _retrieve(ctx, job_id, topics)
    if not hits:
        return not_evaluated(topics, "no lending reference passages were retrieved"), [], []
    claims = claim_index(draft)
    chunks = {h.chunk_id: h for h in hits}
    prompt = ctx.prompt("compliance")
    try:
        review, calls = await call_structured(
            ctx,
            node="compliance",
            route="compliance",
            prompt=prompt,
            payload={
                "draft_recommendation": draft.draft_recommendation,
                "claims": [{"claim_id": k, "text": v} for k, v in claims.items()],
                "topics": [{"rule_id": t.rule_id, "question": t.question} for t in topics],
                "references": [
                    {"chunk_id": h.chunk_id, "doc_id": h.doc_id, "text": h.text} for h in hits
                ],
            },
            schema=ComplianceReview,
            recording_key=f"compliance/{prompt.version}/{package.package_id}"
            + (f".r{revision}" if revision else ""),
            validate=lambda r: validate_review(r, topics, set(claims), set(chunks)),
        )
    except (AgentOutputError, ProviderError, FailClosed) as exc:
        return not_evaluated(topics, f"the reviewer could not run ({type(exc).__name__})"), hits, []
    findings = []
    for a in sorted(review.assessments, key=lambda a: [t.rule_id for t in topics].index(a.rule_id)):
        topic = TOPIC_BY_ID[a.rule_id]
        findings.append(
            ComplianceFinding(
                rule_id=topic.rule_id,
                policy_ref=topic.reference,
                outcome=a.outcome,
                severity=topic.severity if a.outcome == "triggered" else Severity.INFO,
                message=a.message,
                source="compliance_reviewer",
                citations=tuple(
                    Citation(doc_id=chunks[cid].doc_id, locator=cid)
                    for cid in a.reference_chunk_ids
                ),
            )
        )
    return findings, hits, calls
