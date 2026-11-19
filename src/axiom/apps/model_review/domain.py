"""Model documentation review: the documentation package and the checklist's results."""

from __future__ import annotations

from decimal import Decimal
from typing import Literal, get_args

from pydantic import field_validator

from axiom.core.types import (
    MAX_HUMAN_REVISIONS,
    Citation,
    Claim,
    Document,
    Frozen,
    ReviewDecision,
)

DocumentKind = Literal["development_report", "data_description", "monitoring_plan"]
DOCUMENT_KINDS: tuple[str, ...] = get_args(DocumentKind)
SectionOutcome = Literal["present", "empty", "missing"]


class DocumentationPackage(Frozen):
    package_id: str
    model_name: str
    model_type: str
    documents: tuple[Document, ...]

    @field_validator("documents")
    @classmethod
    def _known_kinds(cls, documents: tuple[Document, ...]) -> tuple[Document, ...]:
        unknown = sorted({d.kind for d in documents} - set(DOCUMENT_KINDS))
        if unknown:
            raise ValueError(f"unknown document kinds: {', '.join(unknown)}")
        return documents


class Section(Frozen):
    """A `## ` heading in a document and the lines under it, numbered from 1."""

    doc_id: str
    heading: str
    heading_line: int
    first_line: int
    last_line: int
    content_lines: int


class ProvisionRef(Frozen):
    framework: str
    provision_id: str


class ChecklistItem(Frozen):
    item_id: str
    title: str
    expects: str  # what the section should cover; the assessor judges against it
    document: DocumentKind
    headings: tuple[str, ...]
    critical: bool
    provisions: tuple[ProvisionRef, ...]


class Checklist(Frozen):
    version: str
    min_lines: int
    items: tuple[ChecklistItem, ...]


class SectionCheck(Frozen):
    """Whether one checklist item's section is in the documentation. Decided by code."""

    item_id: str
    outcome: SectionOutcome
    critical: bool
    doc_id: str | None = None
    heading: str | None = None
    # The section's content lines, as a citation locator range: "lines 26-28".
    first_line: int | None = None
    last_line: int | None = None


class Completeness(Frozen):
    checklist_version: str
    present: int
    total: int
    score: Decimal
    empty: tuple[str, ...]
    missing: tuple[str, ...]
    critical_gaps: tuple[str, ...]


Adequacy = Literal["adequate", "weak"]
# What the documentation's state calls for, decided by code from the checklist and the
# assessments: a critical gap is a major gap, any other gap or weak section needs
# remediation. It is never a decision about the model; the validator decides.
DocumentationStatus = Literal["no_material_gaps", "remediation_needed", "major_gaps"]
REPORT_HEADINGS = ("Summary", "Checklist gaps", "Section assessments", "Matters for the validator")


class SectionAssessment(Frozen):
    """A model's judgment of one present section against what the checklist expects."""

    item_id: str
    adequacy: Adequacy
    reason: str
    citations: tuple[Citation, ...]


class Assessments(Frozen):
    assessments: tuple[SectionAssessment, ...]


class ReportSection(Frozen):
    heading: str
    claims: tuple[Claim, ...]


class FindingsReport(Frozen):
    """The drafted findings for the validator. Every claim cites a document line or a
    checklist provision (doc_id is the framework, locator the provision ID)."""

    sections: tuple[ReportSection, ...]


__all__ = [
    "MAX_HUMAN_REVISIONS",
    "Citation",
    "Claim",
    "Document",
    "ReviewDecision",
]
