"""Model documentation review's public schemas: the report, its job and its evidence bundle."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from axiom.api.schemas import GuardrailOut, JobError, ReviewOut, Revisions, SectionOut
from axiom.apps.model_review.domain import DocumentationStatus
from axiom.core.types import ModelCall
from axiom.evidence.log import ChainCheck, EvidenceEvent
from axiom.guardrails import GuardrailResult, latest
from axiom.jobs.store import JobRecord, JobStatus
from axiom.mcp_clients.client import ToolCall

DISCLAIMER = (
    "Draft findings for the model validator. This is not a validation decision about the model."
)

DISCLAIMERS = {
    "awaiting_review": DISCLAIMER,
    "approved": "Approved by a model validator, who is accountable for the findings. "
    "The report was drafted with platform assistance.",
    "rejected": "Rejected by a model validator. The report was drafted with platform assistance.",
}

REPORT_STATUSES = tuple(DISCLAIMERS)


class ModelReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    package_id: str = Field(pattern=r"^mdl-\d{2}$", description="An ID from the synthetic catalog")


class SectionCheckOut(BaseModel):
    """Whether a checklist item's section is in the documentation. Decided by code."""

    item_id: str
    outcome: Literal["present", "empty", "missing"]
    critical: bool
    doc_id: str | None = None
    heading: str | None = None
    first_line: int | None = None
    last_line: int | None = None


class CompletenessOut(BaseModel):
    checklist_version: str
    present: int
    total: int
    score: str
    empty: list[str]
    missing: list[str]
    critical_gaps: list[str]


class AssessmentOut(BaseModel):
    """A model's judgment of one present section against what the checklist expects."""

    item_id: str
    adequacy: Literal["adequate", "weak"]
    reason: str
    citations: list[dict[str, str]]


class ReportOut(BaseModel):
    disclaimer: str = DISCLAIMER
    checklist_version: str
    documentation_status: DocumentationStatus = Field(
        description="Decided by code from the checklist and the assessments: a critical gap is "
        "a major gap, any other gap or weak section needs remediation. Not a decision."
    )
    sections: list[SectionOut]
    checks: list[SectionCheckOut]
    completeness: CompletenessOut
    assessments: list[AssessmentOut]
    provisions_status: str | None = None
    guardrails: list[GuardrailOut] = Field(
        default_factory=list,
        description="Each safety check's latest result. Every run is in the evidence bundle",
    )
    source_hashes: dict[str, str]


class ReviewJob(BaseModel):
    job_id: str
    status: JobStatus
    package_id: str
    created_at: datetime
    updated_at: datetime
    report: ReportOut | None = None
    reviews: list[ReviewOut] = Field(default_factory=list)
    revisions: Revisions
    error: JobError | None = None


class ModelPackageOut(BaseModel):
    package_id: str
    model_name: str
    model_type: str


class ProvisionOut(BaseModel):
    framework: str
    provision_id: str


class ChecklistItemOut(BaseModel):
    item_id: str
    title: str
    expects: str
    document: str
    critical: bool
    provisions: list[ProvisionOut]


class ChecklistOut(BaseModel):
    version: str
    min_lines: int = Field(description="Content lines a section needs to count as present")
    items: list[ChecklistItemOut]


def to_public(job: JobRecord) -> ReviewJob:
    report = None
    r: dict[str, Any] = job.result or {}
    if job.status in REPORT_STATUSES and r.get("report"):
        completeness = r["completeness"]
        report = ReportOut(
            disclaimer=DISCLAIMERS[job.status],
            checklist_version=completeness["checklist_version"],
            documentation_status=r["documentation_status"],
            sections=r["report"]["sections"],
            checks=r["checks"],
            completeness=CompletenessOut(**{**completeness, "score": str(completeness["score"])}),
            assessments=r.get("assessments", []),
            provisions_status=r.get("provisions_status"),
            guardrails=[
                GuardrailOut(**g.model_dump(include=set(GuardrailOut.model_fields)))
                for g in latest(
                    [GuardrailResult.model_validate(g) for g in r.get("guardrails", [])]
                )
            ],
            source_hashes=r.get("source_hashes", {}),
        )
    return ReviewJob(
        job_id=job.job_id,
        status=job.status,
        package_id=job.package_id,
        created_at=job.created_at,
        updated_at=job.updated_at,
        report=report,
        reviews=[
            ReviewOut(round=v["round"], decision=v["decision"], comments=v["comments"])
            for v in r.get("reviews", [])
        ],
        revisions=Revisions(human=job.human_revisions, automated=int(r.get("automated_fixes", 0))),
        error=JobError(code=job.error_code) if job.error_code else None,
    )


class CitationRecord(BaseModel):
    section: str
    claim: str
    doc_id: str
    locator: str


class ProvisionRecord(BaseModel):
    """A provision the governance library returned for a flagged checklist item."""

    item_id: str
    framework: str
    provision_id: str
    citation: str


class ReviewEvidenceBundle(BaseModel):
    """Everything an auditor needs to reconstruct how a findings report was produced."""

    job_id: str
    package_id: str
    submitter: str
    status: JobStatus
    created_at: datetime
    error_code: str | None
    trace_id: str | None
    checklist_version: str | None
    prompt_versions: dict[str, str] = Field(default_factory=dict)
    source_hashes: dict[str, str]
    documentation_status: DocumentationStatus | None = None
    model_calls: list[ModelCall]
    tool_calls: list[ToolCall] = Field(default_factory=list)
    guardrails: list[GuardrailResult] = Field(default_factory=list)
    provisions_status: str | None = None
    provisions: list[ProvisionRecord] = Field(default_factory=list)
    citations: list[CitationRecord]
    approved_report_sha256: str | None = None
    events: list[EvidenceEvent]
    chain: ChainCheck


def to_evidence(
    job: JobRecord, events: list[EvidenceEvent], chain: ChainCheck
) -> ReviewEvidenceBundle:
    r: dict[str, Any] = job.result or {}
    citations = [
        CitationRecord(
            section=section["heading"],
            claim=claim["text"],
            doc_id=c["doc_id"],
            locator=c["locator"],
        )
        for section in (r.get("report") or {}).get("sections", [])
        for claim in section["claims"]
        for c in claim["citations"]
    ]
    return ReviewEvidenceBundle(
        job_id=job.job_id,
        package_id=job.package_id,
        submitter=job.submitter,
        status=job.status,
        created_at=job.created_at,
        error_code=job.error_code,
        trace_id=r.get("trace_id"),
        checklist_version=r.get("checklist_version"),
        prompt_versions=r.get("prompt_versions", {}),
        source_hashes=r.get("source_hashes", {}),
        documentation_status=r.get("documentation_status"),
        model_calls=r.get("model_calls", []),
        tool_calls=r.get("tool_calls", []),
        guardrails=r.get("guardrails", []),
        provisions_status=r.get("provisions_status"),
        provisions=[
            ProvisionRecord(
                item_id=item_id,
                framework=p["framework"],
                provision_id=p["provision_id"],
                citation=p["citation"],
            )
            for item_id, found in (r.get("provisions") or {}).items()
            for p in found
        ],
        citations=citations,
        approved_report_sha256=r.get("approved_report_sha256"),
        events=events,
        chain=chain,
    )
