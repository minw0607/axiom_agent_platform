"""Typed domain objects shared by the API, the workflow and the tools.

Every financial figure carries a source location. Money values are Decimal so the
deterministic tools are exact; the API serializes them as strings.
"""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

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


class Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Document(Frozen):
    doc_id: str
    kind: DocumentKind
    title: str
    text: str


class BorrowerPackage(Frozen):
    """A borrower's submission: identity, industry and source documents."""

    package_id: str
    borrower_name: str
    industry: str
    documents: tuple[Document, ...]

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


class CheckResult(Frozen):
    """Outcome of a deterministic check. A check that cannot run is not_evaluated."""

    name: str
    outcome: Literal["passed", "triggered", "not_evaluated"]
    detail: str
    year: int | None = None


class Citation(Frozen):
    doc_id: str
    locator: str = Field(description="'line N' for a source document, or a GKN chunk id")


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


class Claim(Frozen):
    text: str
    citations: tuple[Citation, ...] = ()


class MemoSection(Frozen):
    heading: str
    claims: tuple[Claim, ...]


class MemoDraft(Frozen):
    sections: tuple[MemoSection, ...]
    draft_recommendation: Literal["approve", "approve_with_conditions", "decline", "refer"]
