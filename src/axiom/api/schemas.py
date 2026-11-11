"""API schemas. The API is the contract: no model, agent or prompt names in public responses.

The evidence bundle is the one exception. It is for auditors only and names the model alias,
provider, prompt and version behind every call, never a real deployment name.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from axiom.agents.base import ModelCall
from axiom.evidence.log import ChainCheck, EvidenceEvent
from axiom.jobs.store import JobRecord, JobStatus

DISCLAIMER = "Draft for credit officer review. This is not a credit decision."


class MemoRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    package_id: str = Field(pattern=r"^pkg-\d{2}$", description="An ID from the synthetic catalog")


class Links(BaseModel):
    self: str


class JobAccepted(BaseModel):
    job_id: str
    status: JobStatus
    links: Links


class SourceOut(BaseModel):
    doc_id: str
    line: int


class FigureOut(BaseModel):
    value: str
    source: SourceOut


class RatioOut(BaseModel):
    name: str
    year: int
    value: str | None
    formula: str
    note: str | None = None


class FlagOut(BaseModel):
    code: str
    severity: str
    message: str
    year: int | None = None
    policy_ref: str | None = None


class CheckOut(BaseModel):
    name: str
    outcome: Literal["passed", "triggered", "not_evaluated"]
    detail: str
    year: int | None = None


class CitationOut(BaseModel):
    doc_id: str
    locator: str


class ClaimOut(BaseModel):
    text: str
    citations: list[CitationOut]


class SectionOut(BaseModel):
    heading: str
    claims: list[ClaimOut]


class MemoOut(BaseModel):
    disclaimer: str = DISCLAIMER
    policy_version: str
    draft_recommendation: str
    sections: list[SectionOut]
    figures: dict[str, dict[str, FigureOut]]
    loan_request: dict[str, FigureOut]
    ratios: list[RatioOut]
    risk_flags: list[FlagOut]
    reconciliation: list[CheckOut]
    source_hashes: dict[str, str]


class JobError(BaseModel):
    code: str


class MemoJob(BaseModel):
    job_id: str
    status: JobStatus
    package_id: str
    created_at: datetime
    updated_at: datetime
    memo: MemoOut | None = None
    error: JobError | None = None


def to_public(job: JobRecord) -> MemoJob:
    memo = None
    r: dict[str, Any] | None = job.result
    if job.status == "awaiting_review" and r and r.get("draft"):
        fs = r["financials"]
        memo = MemoOut(
            policy_version=r["policy_version"],
            draft_recommendation=r["draft"]["draft_recommendation"],
            sections=r["draft"]["sections"],
            figures={str(y): fields for y, fields in fs["years"].items()},
            loan_request=fs["loan"],
            ratios=r["ratios"],
            risk_flags=r["risk_flags"],
            reconciliation=r["checks"],
            source_hashes=r.get("source_hashes", {}),
        )
    return MemoJob(
        job_id=job.job_id,
        status=job.status,
        package_id=job.package_id,
        created_at=job.created_at,
        updated_at=job.updated_at,
        memo=memo,
        error=JobError(code=job.error_code) if job.error_code else None,
    )


class CitationRecord(BaseModel):
    section: str
    claim: str
    doc_id: str
    locator: str


class RetrievedPassage(BaseModel):
    collection: str
    doc_id: str
    chunk_id: str
    text_sha256: str


class EvidenceBundle(BaseModel):
    """Everything an auditor needs to reconstruct how a memo was produced."""

    job_id: str
    package_id: str
    submitter: str
    status: JobStatus
    created_at: datetime
    error_code: str | None
    trace_id: str | None
    policy_version: str | None
    source_hashes: dict[str, str]
    model_calls: list[ModelCall]
    retrieved: list[RetrievedPassage]
    citations: list[CitationRecord]
    events: list[EvidenceEvent]
    chain: ChainCheck


def to_evidence(job: JobRecord, events: list[EvidenceEvent], chain: ChainCheck) -> EvidenceBundle:
    r: dict[str, Any] = job.result or {}
    citations = [
        CitationRecord(
            section=section["heading"],
            claim=claim["text"],
            doc_id=c["doc_id"],
            locator=c["locator"],
        )
        for section in (r.get("draft") or {}).get("sections", [])
        for claim in section["claims"]
        for c in claim["citations"]
    ]
    return EvidenceBundle(
        job_id=job.job_id,
        package_id=job.package_id,
        submitter=job.submitter,
        status=job.status,
        created_at=job.created_at,
        error_code=job.error_code,
        trace_id=r.get("trace_id"),
        policy_version=r.get("policy_version"),
        source_hashes=r.get("source_hashes", {}),
        model_calls=r.get("model_calls", []),
        retrieved=[
            RetrievedPassage(
                collection=h["collection"],
                doc_id=h["doc_id"],
                chunk_id=h["chunk_id"],
                text_sha256=h["text_hash"],
            )
            for h in r.get("evidence", [])
        ],
        citations=citations,
        events=events,
        chain=chain,
    )
