"""API schemas. The API is the contract: no model, agent or prompt names in public responses.

The evidence bundle is the one exception. It is for auditors only and names the model alias,
provider, prompt and version behind every call, never a real deployment name.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from axiom.jobs.store import JobStatus, JobSummary


class ReviewRequest(BaseModel):
    # The API refuses a revise with blank comments (comments-required); the schema says so.
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "if": {"properties": {"decision": {"const": "revise"}}},
            "then": {"required": ["comments"], "properties": {"comments": {"pattern": r"\S"}}},
        },
    )
    decision: Literal["approve", "reject", "revise"]
    comments: str = Field(
        default="",
        max_length=2000,
        description="Required for revise: what the credit officer wants changed",
    )


class Health(BaseModel):
    status: Literal["ok"]


class Readiness(BaseModel):
    status: Literal["ready", "not_ready"]


class Links(BaseModel):
    self: str


class JobAccepted(BaseModel):
    job_id: str
    status: JobStatus
    links: Links


class CitationOut(BaseModel):
    doc_id: str
    locator: str


class ClaimOut(BaseModel):
    text: str
    citations: list[CitationOut]


class SectionOut(BaseModel):
    heading: str
    claims: list[ClaimOut]


class GuardrailOut(BaseModel):
    """A safety check's result for the current draft. not_evaluated means it did not run."""

    control: str
    stage: str
    outcome: Literal["passed", "triggered", "not_evaluated"]
    action: Literal["none", "redacted", "quarantined"] = "none"
    reason: str


class JobError(BaseModel):
    code: str


class ReviewOut(BaseModel):
    """One credit officer decision. Who made it is in the evidence bundle."""

    round: int
    decision: str
    comments: str


class Revisions(BaseModel):
    human: int = Field(description="Revisions a credit officer asked for (at most 2)")
    automated: int = Field(description="Automated fixes by guardrails, counted separately")


class ReviewAccepted(BaseModel):
    job_id: str
    status: JobStatus
    round: int
    links: Links


class Me(BaseModel):
    name: str
    role: Literal["submitter", "reviewer", "auditor"]


class MemoSummary(BaseModel):
    """One row of GET /v1/memos. Who submitted it is in the evidence bundle."""

    job_id: str
    status: JobStatus
    package_id: str
    created_at: datetime
    updated_at: datetime
    draft_recommendation: str | None
    human_revisions: int


class MemoList(BaseModel):
    items: list[MemoSummary]


def to_summary(job: JobSummary) -> MemoSummary:
    return MemoSummary(**job.model_dump(exclude={"submitter"}))


class AppOut(BaseModel):
    """An enabled app: its routes are under /v1/apps/{app_id}/jobs."""

    app_id: str
    title: str
    roles: dict[Literal["submitter", "reviewer", "auditor"], str] = Field(
        description="What each platform role is called in this app"
    )
