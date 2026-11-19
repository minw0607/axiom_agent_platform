"""Typed domain objects shared by the API, the workflow and the tools.

Every financial figure carries a source location. Money values are Decimal so the
deterministic tools are exact; the API serializes them as strings.
"""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from typing import Literal, get_args

from pydantic import Field, field_validator

from axiom.core.types import (
    MAX_HUMAN_REVISIONS,
    CheckResult,
    Citation,
    Claim,
    Document,
    Frozen,
    ReviewDecision,
)

# Line items extracted from each statement, per fiscal year.
INCOME_STATEMENT_FIELDS = (
    "revenue",
    "cost_of_goods_sold",
    "operating_expenses",
    "ebitda",
    "depreciation_amortization",
    "interest_expense",
    "income_tax",
    "net_income",
)
BALANCE_SHEET_FIELDS = (
    "cash",
    "accounts_receivable",
    "inventory",
    "total_current_assets",
    "total_assets",
    "total_current_liabilities",
    "total_debt",
    "total_liabilities",
    "total_equity",
)
CASH_FLOW_FIELDS = (
    "capital_expenditures",
    "debt_principal_repaid",
)
# Stated in the business description, for the latest fiscal year (credit policy CP-5).
BUSINESS_DESCRIPTION_FIELDS = ("largest_customer_share_pct",)
YEARLY_FIELDS = (
    INCOME_STATEMENT_FIELDS + BALANCE_SHEET_FIELDS + CASH_FLOW_FIELDS + BUSINESS_DESCRIPTION_FIELDS
)
LOAN_FIELDS = ("requested_amount", "interest_rate_pct", "term_years", "amortization_years")

DocumentKind = Literal[
    "income_statement", "balance_sheet", "cash_flow", "loan_application", "business_description"
]
DOCUMENT_KINDS: tuple[str, ...] = get_args(DocumentKind)

# The core types this module re-exports, so app code and checkpoints written before
# ADR 0016 (as axiom.domain.<name>) still find them here.
__all__ = [
    "MAX_HUMAN_REVISIONS",
    "CheckResult",
    "Citation",
    "Claim",
    "Document",
    "Frozen",
    "ReviewDecision",
]


class BorrowerPackage(Frozen):
    """A borrower's submission: identity, industry and source documents."""

    package_id: str
    borrower_name: str
    industry: str
    documents: tuple[Document, ...]

    @field_validator("documents")
    @classmethod
    def _known_kinds(cls, documents: tuple[Document, ...]) -> tuple[Document, ...]:
        for d in documents:
            if d.kind not in DOCUMENT_KINDS:
                raise ValueError(f"unknown document kind {d.kind!r}")
        return documents

    def document(self, kind: DocumentKind) -> Document | None:
        return next((d for d in self.documents if d.kind == kind), None)


class SourceRef(Frozen):
    """Where a figure came from: a document and a 1-based line number."""

    doc_id: str
    line: int = Field(ge=1)


class Figure(Frozen):
    value: Decimal
    source: SourceRef


class LoanRequest(Frozen):
    requested_amount: Figure
    interest_rate_pct: Figure
    term_years: Figure
    amortization_years: Figure


class FinancialStatements(Frozen):
    """Extracted figures keyed by fiscal year, then by field name."""

    years: dict[int, dict[str, Figure]]
    loan: LoanRequest

    def fiscal_years(self) -> list[int]:
        return sorted(self.years)

    def loan_figures(self) -> list[Figure]:
        return [
            self.loan.requested_amount,
            self.loan.interest_rate_pct,
            self.loan.term_years,
            self.loan.amortization_years,
        ]

    def get(self, year: int, field: str) -> Decimal | None:
        figure = self.years.get(year, {}).get(field)
        return None if figure is None else figure.value


class RatioResult(Frozen):
    """One deterministic ratio. `value` is None when the ratio is undefined."""

    name: str
    year: int
    value: Decimal | None
    formula: str
    inputs: dict[str, Decimal]
    note: str | None = None


class Severity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    HIGH = "high"


class RiskFlag(Frozen):
    code: str
    severity: Severity
    message: str
    year: int | None = None
    ratio: str | None = None
    policy_ref: str | None = None


class ComplianceFinding(Frozen):
    """One credit policy rule or lending-reference check applied to a draft.

    A drafting aid for the credit officer, not a legal conclusion. Reference-based findings
    cite the lending-reference chunks they rest on.
    """

    rule_id: str
    policy_ref: str
    outcome: Literal["passed", "triggered", "not_evaluated"]
    severity: Severity
    message: str
    source: Literal["rule_engine", "compliance_reviewer"] = "rule_engine"
    citations: tuple[Citation, ...] = ()


class MemoSection(Frozen):
    heading: str
    claims: tuple[Claim, ...]


class MemoDraft(Frozen):
    sections: tuple[MemoSection, ...]
    draft_recommendation: Literal["approve", "approve_with_conditions", "decline", "refer"]
