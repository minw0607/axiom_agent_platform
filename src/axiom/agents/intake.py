"""Intake: extract figures from each document, verify every figure against its source line.

The model copies figures; code checks each one. A figure counts only if its value appears
on the cited line, in the column for the cited fiscal year.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from pydantic import BaseModel

from axiom.agents.base import AgentContext, ModelCall, call_structured
from axiom.domain import (
    BALANCE_SHEET_FIELDS,
    CASH_FLOW_FIELDS,
    INCOME_STATEMENT_FIELDS,
    LOAN_FIELDS,
    BorrowerPackage,
    Document,
    Figure,
    FinancialStatements,
    LoanRequest,
    SourceRef,
)

DOC_FIELDS: dict[str, tuple[str, ...]] = {
    "income_statement": INCOME_STATEMENT_FIELDS,
    "balance_sheet": BALANCE_SHEET_FIELDS,
    "cash_flow": CASH_FLOW_FIELDS,
    "loan_application": LOAN_FIELDS,
}
_NUMBER = re.compile(r"\(?-?\$?\d[\d,]*(?:\.\d+)?%?\)?")
_FY = re.compile(r"FY(\d{4})")


class IntakeError(RuntimeError):
    pass


class ExtractedFigure(BaseModel):
    field: str
    year: int | None
    value: str
    line: int


class ExtractionOutput(BaseModel):
    figures: list[ExtractedFigure]


def numbered(text: str) -> str:
    return "\n".join(f"{i}: {line}" for i, line in enumerate(text.splitlines(), 1))


def parse_number(token: str) -> Decimal | None:
    negative = token.startswith("(") and token.endswith(")")
    cleaned = token.strip("()").replace(",", "").replace("$", "").replace("%", "")
    try:
        value = Decimal(cleaned)
    except InvalidOperation:
        return None
    return -value if negative else value


def _year_columns(lines: list[str]) -> list[int] | None:
    for line in lines:
        if line.startswith("|") and _FY.search(line):
            return [int(y) for y in _FY.findall(line)]
    return None


def verify_figure(doc: Document, fig: ExtractedFigure, allowed: tuple[str, ...]) -> str | None:
    """Return an error message, or None when the figure is supported by its source line."""
    lines = doc.text.splitlines()
    label = f"{fig.field}/{fig.year}"
    if fig.field not in allowed:
        return f"{label}: field not requested for this document"
    is_loan = fig.field in LOAN_FIELDS
    if is_loan and fig.year is not None:
        return f"{label}: loan terms take year null"
    if not is_loan and fig.year is None:
        return f"{label}: a fiscal year is required"
    value = parse_number(fig.value)
    if value is None:
        return f"{label}: value {fig.value!r} is not a number"
    if not 1 <= fig.line <= len(lines):
        return f"{label}: line {fig.line} does not exist"
    line = lines[fig.line - 1]
    years = _year_columns(lines)
    if years is not None and fig.year is not None and line.startswith("|"):
        if fig.year not in years:
            return f"{label}: the document has no column for {fig.year}"
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        cell = cells[1 + years.index(fig.year)] if len(cells) > len(years) else ""
        if parse_number(cell) != value:
            return f"{label}: line {fig.line} shows {cell!r} for FY{fig.year}, not {fig.value}"
        return None
    if value not in {parse_number(t) for t in _NUMBER.findall(line)}:
        return f"{label}: {fig.value} does not appear on line {fig.line}"
    return None


async def extract_document(
    ctx: AgentContext, package: BorrowerPackage, doc: Document
) -> tuple[list[ExtractedFigure], list[ModelCall]]:
    allowed = DOC_FIELDS[doc.kind]
    prompt = ctx.prompt("intake")

    def validate(out: ExtractionOutput) -> list[str]:
        errors = [e for f in out.figures if (e := verify_figure(doc, f, allowed))]
        seen: set[tuple[str, int | None]] = set()
        for f in out.figures:
            if (f.field, f.year) in seen:
                errors.append(f"{f.field}/{f.year}: extracted more than once")
            seen.add((f.field, f.year))
        return errors

    out, calls = await call_structured(
        ctx,
        node="intake",
        route="extraction",
        prompt=prompt,
        payload={
            "document": {"doc_id": doc.doc_id, "kind": doc.kind, "lines": numbered(doc.text)},
            "fields": list(allowed),
        },
        schema=ExtractionOutput,
        recording_key=f"intake/{prompt.version}/{package.package_id}/{doc.kind}",
        validate=validate,
    )
    return out.figures, calls


async def run_intake(
    ctx: AgentContext, package: BorrowerPackage
) -> tuple[FinancialStatements, list[ModelCall]]:
    years: dict[int, dict[str, Figure]] = {}
    loan: dict[str, Figure] = {}
    calls: list[ModelCall] = []
    for doc in package.documents:
        if doc.kind not in DOC_FIELDS:
            continue
        figures, doc_calls = await extract_document(ctx, package, doc)
        calls += doc_calls
        for f in figures:
            figure = Figure(
                value=parse_number(f.value) or Decimal(0),
                source=SourceRef(doc_id=doc.doc_id, line=f.line),
            )
            if f.year is None:
                loan[f.field] = figure
            else:
                years.setdefault(f.year, {})[f.field] = figure
    missing = [f for f in LOAN_FIELDS if f not in loan]
    if missing:
        raise IntakeError(f"loan terms missing from the application: {', '.join(missing)}")
    if not any("revenue" in fields for fields in years.values()):
        raise IntakeError("no fiscal year with revenue was found")
    return FinancialStatements(years=years, loan=LoanRequest(**loan)), calls
