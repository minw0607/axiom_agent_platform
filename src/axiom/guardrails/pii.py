"""PII redaction on borrower documents, before they are indexed or sent to a model.

Presidio finds personal identifiers (names, contact details, government and account
numbers) and each one is replaced with its type, such as `[PERSON]`. Three rules keep the
credit analysis intact:

1. Financial values are never changed. A table row is never edited, and a finding that
   overlaps an amount, percentage or fiscal year is dropped. A final check compares every
   financial value before and after, and refuses the redaction if any differ.
2. The borrower's business name is not personal data and is never redacted.
3. Line breaks are kept, so every figure's source line number still points to the same line.

Business facts such as dates, places and organizations are left alone: only identifiers of
people are redacted. Results record counts by type, never the redacted text.

Known limit: the small English model mistakes single capitalized words for names, so a
name must be at least two capitalized words. A lone surname ("Lopez") is not redacted.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from functools import cache
from typing import Any

from axiom.domain import Document
from axiom.guardrails.base import Stage, Subject, Verdict

# Presidio entity types that identify a person. Dates, places and organizations stay.
ENTITIES = (
    "PERSON",
    "EMAIL_ADDRESS",
    "PHONE_NUMBER",
    "US_SSN",
    "US_ITIN",
    "US_PASSPORT",
    "US_DRIVER_LICENSE",
    "US_BANK_NUMBER",
    "CREDIT_CARD",
    "IBAN_CODE",
    "IP_ADDRESS",
)
# Weak number patterns (bank, passport, licence) only reach this score with a context word
# such as "account" nearby, so a bare number is not redacted.
MIN_SCORE = 0.4

# Amounts with thousands separators or cents, dollar amounts, percentages, fiscal years.
FINANCIAL = re.compile(
    r"\$?\(?\d{1,3}(?:,\d{3})+(?:\.\d+)?\)?"
    r"|\$\d+(?:\.\d+)?"
    r"|\b\d+\.\d{2}\b(?!\.\d)"
    r"|\b\d+(?:\.\d+)?\s?%"
    r"|\bFY\s?\d{4}\b"
)


# A person's name, as the small model finds it reliably: two or more capitalized words,
# each with a lowercase letter, so codes such as "REF AXC-01" are not taken for names.
_WORD = r"[A-Z](?=[\w'.-]*[a-z])[\w'.-]+"
FULL_NAME = re.compile(rf"{_WORD}(?:\s+{_WORD})+")


class RedactionError(RuntimeError):
    """Redaction would have changed a financial value. The documents are not used."""


@cache
def _analyzer() -> Any:
    # Loaded once per process (about a second); the small English model keeps the image lean.
    from presidio_analyzer import AnalyzerEngine
    from presidio_analyzer.nlp_engine import NlpEngineProvider

    nlp = NlpEngineProvider(
        nlp_configuration={
            "nlp_engine_name": "spacy",
            "models": [{"lang_code": "en", "model_name": "en_core_web_sm"}],
        }
    ).create_engine()
    return AnalyzerEngine(nlp_engine=nlp, supported_languages=["en"])


@dataclass
class Redacted:
    text: str
    counts: Counter[str] = field(default_factory=Counter)


def _protected(line: str, borrower_name: str) -> list[tuple[int, int]]:
    spans = [m.span() for m in FINANCIAL.finditer(line)]
    if borrower_name:
        start = line.find(borrower_name)
        while start != -1:
            spans.append((start, start + len(borrower_name)))
            start = line.find(borrower_name, start + 1)
    return spans


def redact_line(line: str, borrower_name: str = "") -> Redacted:
    if not line.strip() or line.lstrip().startswith(("|", "#")):
        # Blank lines, table rows (the statements) and headings (titles) are never edited.
        return Redacted(line)
    protected = _protected(line, borrower_name)
    found = _analyzer().analyze(
        line, language="en", entities=list(ENTITIES), score_threshold=MIN_SCORE
    )
    keep: list[Any] = []
    for r in sorted(found, key=lambda r: (-r.score, r.start)):
        if r.entity_type == "PHONE_NUMBER" and line[r.start : r.end].isdigit():
            continue  # a bare digit run such as 342657 is an amount, not a phone number
        if any(r.start < e and s < r.end for s, e in protected):
            continue
        if r.entity_type == "PERSON" and not FULL_NAME.fullmatch(line[r.start : r.end]):
            continue  # the small model tags single words such as "Loan" as names
        if any(r.start < k.end and k.start < r.end for k in keep):
            continue  # overlapping findings: the higher score wins
        keep.append(r)
    out, counts = line, Counter[str]()
    for r in sorted(keep, key=lambda r: r.start, reverse=True):
        out = f"{out[: r.start]}[{r.entity_type}]{out[r.end :]}"
        counts[r.entity_type] += 1
    return Redacted(out, counts)


def financial_values(text: str) -> list[str]:
    return [m.group() for m in FINANCIAL.finditer(text)]


def redact_document(doc: Document, borrower_name: str = "") -> Redacted:
    lines = [redact_line(line, borrower_name) for line in doc.text.split("\n")]
    text = "\n".join(r.text for r in lines)
    if financial_values(text) != financial_values(doc.text):
        raise RedactionError(f"redaction would change a financial value in {doc.doc_id}")
    return Redacted(text, sum((r.counts for r in lines), Counter[str]()))


@dataclass(frozen=True)
class PIIRedaction:
    name: str = "pii_redaction"
    stage: Stage = "input"
    description: str = (
        "Masks personal identifiers in borrower documents; financial values are never changed"
    )

    async def check(self, subject: Subject) -> Verdict:
        documents: list[Document] = []
        by_doc: dict[str, dict[str, int]] = {}
        for doc in subject.documents:
            r = redact_document(doc, subject.borrower_name)
            documents.append(doc.model_copy(update={"text": r.text}) if r.counts else doc)
            if r.counts:
                by_doc[doc.doc_id] = dict(sorted(r.counts.items()))
        total = sum(sum(c.values()) for c in by_doc.values())
        if not total:
            return Verdict("passed", "no personal identifiers found", documents=tuple(documents))
        return Verdict(
            "triggered",
            f"masked {total} personal identifier{'s' if total != 1 else ''} "
            f"in {len(by_doc)} document{'s' if len(by_doc) != 1 else ''}",
            details={"redacted": by_doc},
            action="redacted",
            documents=tuple(documents),
        )


PII_REDACTION = PIIRedaction()
