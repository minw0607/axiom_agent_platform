"""Model documentation review: the documentation package and the checklist's results."""

from __future__ import annotations

from decimal import Decimal
from typing import Literal, get_args

from pydantic import field_validator

from axiom.core.types import Document, Frozen

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
