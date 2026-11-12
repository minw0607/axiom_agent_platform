"""Checks on each draft: three policy rails (generation) and two evidence checks (output).

Rails, on every claim's text:
- `decision_language`: the draft states a credit decision as made ("the loan is approved").
  The recommendation is a labeled draft field; only a credit officer decides.
- `investment_advice`: advice to buy, sell or hold securities, or other investment advice.
- `on_task`: text that is not memo content, such as chatbot asides, links, or instructions
  echoed from the documents.

Evidence checks:
- `number_provenance`: every number in the draft must come from the inputs: an extracted
  figure, a ratio or reconciliation total from the tested tools, a policy threshold, or a
  number in the borrower's documents or retrieved passages. A number may be rounded, but
  only as far as its shown precision allows.
- `citation_support`: a claim that states an extracted figure must cite that figure's
  source line, or a retrieved passage of its document, and no citation may point at a blank
  line.

A triggered check sends the draft back to the drafter once as an automated fix (see the
workflow). Results record section and claim positions, pattern codes and the numbers at
issue, never the claim text.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from axiom.domain import Claim, MemoDraft
from axiom.guardrails.base import DraftFacts, GuardrailResult, Stage, Subject, Verdict
from axiom.guardrails.injection import PATTERNS as INJECTION_PATTERNS
from axiom.guardrails.injection import normalize

RAILS: dict[str, dict[str, re.Pattern[str]]] = {
    "decision_language": {
        "decision_stated_as_made": re.compile(
            r"\b(loan|credit|application|request|facility|borrower)\b[^.;]{0,30}"
            r"\b(is|has been|was|are|have been) (hereby |now )?"
            r"(approved|declined|denied|granted|rejected|funded)\b"
        ),
        "first_person_decision": re.compile(
            r"\bwe (hereby )?(approve|decline|deny|grant|reject|will (fund|lend))\b"
        ),
        "final_decision": re.compile(r"\bfinal (credit )?decision\b|\bdecision is final\b"),
    },
    "investment_advice": {
        "securities_advice": re.compile(
            r"\b(buy|sell|hold|short|invest in|purchase)\b[^.;]{0,20}"
            r"\b(stocks?|shares|bonds?|securities|equit(y|ies)|options|crypto\w*)\b"
        ),
        "investment_language": re.compile(
            r"\binvestment (advice|recommendation|opportunity)\b|\bprice target\b"
            r"|\b(portfolio|asset) allocation\b|\bexpected return\b"
        ),
    },
    "on_task": {
        "chatbot_aside": re.compile(
            r"\bas an ai\b|\blanguage model\b|\bi (cannot|can't|am unable)\b|\bi'm sorry\b"
            r"|\blet me know\b|\bhere is (the|your) (memo|draft)\b|\bhappy to help\b"
        ),
        "link": re.compile(r"https?://|www\."),
        **{f"echoed_{code}": p for code, p in INJECTION_PATTERNS.items()},
    },
}


def claims(draft: MemoDraft) -> Iterator[tuple[int, int, Claim]]:
    for i, section in enumerate(draft.sections):
        for j, claim in enumerate(section.claims):
            yield i, j, claim


def _verdict(control: str, issues: list[dict[str, Any]], clean: str) -> Verdict:
    if not issues:
        return Verdict("passed", clean)
    codes = sorted({i["code"] for i in issues})
    n = len({(i["section"], i["claim"]) for i in issues})
    return Verdict(
        "triggered",
        f"{n} claim{'s' if n != 1 else ''}: {', '.join(codes)}",
        details={"issues": issues},
    )


@dataclass(frozen=True)
class Rail:
    name: str
    description: str
    stage: Stage = "generation"

    async def check(self, subject: Subject) -> Verdict:
        if subject.draft is None:
            return Verdict("not_evaluated", "no draft to check")
        issues = [
            {"section": i, "claim": j, "code": code}
            for i, j, claim in claims(subject.draft)
            for code, pattern in RAILS[self.name].items()
            if pattern.search(normalize(claim.text))
        ]
        return _verdict(self.name, issues, "no claim breaks this rule")


DECISION_LANGUAGE = Rail("decision_language", "No credit decision stated as made")
INVESTMENT_ADVICE = Rail("investment_advice", "No investment advice")
ON_TASK = Rail("on_task", "Only memo content: no asides, links or echoed instructions")


# Numbers -------------------------------------------------------------------------------------

# A number not part of an identifier such as FY2025, CP-4.1 or pkg-04, with an optional
# scale word or unit after it.
NUMBER = re.compile(
    r"(?<![\w.\-])(?<![A-Za-z]-)(-?)\$?(\d[\d,]*(?:\.\d+)?)(?![\w-]*[A-Za-z]\d)"
    r"(?:\s?(%|percent\b|x\b|times\b|million\b|thousand\b|billion\b))?"
)
SCALE = {"million": Decimal(10) ** 6, "thousand": Decimal(10) ** 3, "billion": Decimal(10) ** 9}
SMALL_COUNT = 12  # small whole numbers are counts ("two flags", "5 years"), not figures


@dataclass(frozen=True)
class Stated:
    """A number as the draft states it, with the rounding its precision allows."""

    text: str
    value: Decimal
    tolerance: Decimal
    percent: bool


def stated_numbers(text: str) -> list[Stated]:
    out: list[Stated] = []
    for m in NUMBER.finditer(text):
        sign, digits, unit = m.group(1), m.group(2).replace(",", ""), (m.group(3) or "")
        try:
            value = Decimal(sign + digits)
        except InvalidOperation:
            continue
        places = len(digits.partition(".")[2])
        scale = SCALE.get(unit, Decimal(1))
        out.append(
            Stated(
                text=m.group(0).strip(),
                value=value * scale,
                tolerance=Decimal(5) * Decimal(10) ** -(places + 1) * scale,
                percent=unit in ("%", "percent"),
            )
        )
    return out


def claim_numbers(text: str, facts: DraftFacts) -> list[Stated]:
    """The numbers a claim states, leaving out any inside the borrower's name
    ("Synthetic Borrower 14" states no figure)."""
    return stated_numbers(text.replace(facts.package.borrower_name, ""))


def known_values(facts: DraftFacts) -> list[Decimal]:
    values: list[Decimal] = []
    for fields in facts.financials.years.values():
        values += [f.value for f in fields.values()]
    values += [f.value for f in facts.financials.loan_figures()]
    values += [Decimal(y) for y in facts.financials.years]
    for r in facts.ratios:
        if r.value is not None:
            values += [r.value, r.value * 100]  # a ratio may be shown as a percentage
    for t in facts.thresholds:
        values += [t, t * 100]
    texts = [d.text for d in facts.package.documents] + [h.text for h in facts.evidence]
    texts += [c.detail for c in facts.checks]  # the reconciliation tool's computed totals
    for text in texts:
        values += [s.value for s in stated_numbers(text)]
    return values


def supported(n: Stated, known: list[Decimal]) -> bool:
    if n.value == n.value.to_integral_value() and 0 <= n.value <= SMALL_COUNT:
        return True
    candidates = [n.value, n.value / 100] if n.percent else [n.value]
    return any(abs(c - k) <= n.tolerance for c in candidates for k in known)


@dataclass(frozen=True)
class NumberProvenance:
    name: str = "number_provenance"
    stage: Stage = "output"
    description: str = "Every number in the draft comes from the inputs"

    async def check(self, subject: Subject) -> Verdict:
        if subject.draft is None or subject.facts is None:
            return Verdict("not_evaluated", "no draft or inputs to check against")
        known = known_values(subject.facts)
        issues = [
            {"section": i, "claim": j, "code": "unsupported_number", "value": n.text}
            for i, j, claim in claims(subject.draft)
            for n in claim_numbers(claim.text, subject.facts)
            if not supported(n, known)
        ]
        return _verdict(self.name, issues, "every number traces to the inputs")


@dataclass(frozen=True)
class CitationSupport:
    name: str = "citation_support"
    stage: Stage = "output"
    description: str = "Claims cite the source lines of the figures they state"

    async def check(self, subject: Subject) -> Verdict:
        if subject.draft is None or subject.facts is None:
            return Verdict("not_evaluated", "no draft or inputs to check against")
        lines = {d.doc_id: d.text.split("\n") for d in subject.facts.package.documents}
        sources: dict[Decimal, set[tuple[str, str]]] = {}
        for fields in subject.facts.financials.years.values():
            for f in fields.values():
                sources.setdefault(f.value, set()).add((f.source.doc_id, f"line {f.source.line}"))
        for f in subject.facts.financials.loan_figures():
            sources.setdefault(f.value, set()).add((f.source.doc_id, f"line {f.source.line}"))
        issues: list[dict[str, Any]] = []
        for i, j, claim in claims(subject.draft):
            cited = {(c.doc_id, c.locator) for c in claim.citations}
            # A retrieved passage of the figure's document also supports it.
            cited |= {
                (c.doc_id, f"line {line}")
                for c in claim.citations
                if not c.locator.startswith("line ")
                for line in range(1, len(lines.get(c.doc_id, [])) + 1)
            }
            for c in claim.citations:
                n = c.locator.removeprefix("line ")
                if c.locator.startswith("line ") and n.isdigit() and c.doc_id in lines:
                    text = lines[c.doc_id]
                    if 1 <= int(n) <= len(text) and not text[int(n) - 1].strip():
                        issues.append(
                            {"section": i, "claim": j, "code": "blank_line", "value": c.locator}
                        )
            for stated in claim_numbers(claim.text, subject.facts):
                need = sources.get(stated.value)
                if need and stated.value > SMALL_COUNT and not need & cited:
                    issues.append(
                        {"section": i, "claim": j, "code": "figure_not_cited", "value": stated.text}
                    )
        return _verdict(self.name, issues, "figures cite their source lines")


@dataclass(frozen=True)
class PIIOutput:
    """Personal identifiers in the draft. The drafter only sees redacted documents, so these
    would come from the model itself; a finding sends the draft back as an automated fix."""

    name: str = "pii_output"
    stage: Stage = "output"
    description: str = "No personal identifiers in the draft"

    async def check(self, subject: Subject) -> Verdict:
        from axiom.guardrails.pii import redact_line  # loads Presidio on first use

        if subject.draft is None:
            return Verdict("not_evaluated", "no draft to check")
        issues = [
            {"section": i, "claim": j, "code": "personal_data", "value": entity}
            for i, j, claim in claims(subject.draft)
            for entity in sorted(redact_line(claim.text, subject.borrower_name).counts)
        ]
        return _verdict(self.name, issues, "no personal identifiers found")


NUMBER_PROVENANCE = NumberProvenance()
CITATION_SUPPORT = CitationSupport()
PII_OUTPUT = PIIOutput()


PROBLEM = {
    "decision_stated_as_made": "states a credit decision as made; only the credit officer "
    "decides, so describe the draft recommendation instead",
    "first_person_decision": "states a credit decision as made; only the credit officer decides",
    "final_decision": "calls something a final decision; the memo is a draft",
    "securities_advice": "gives investment advice; remove it",
    "investment_language": "gives investment advice; remove it",
    "chatbot_aside": "is not memo content; remove it",
    "link": "contains a link; remove it",
    "unsupported_number": "states {value}, which is not in the inputs",
    "figure_not_cited": "states {value} without citing that figure's source line",
    "blank_line": "cites {value}, which is a blank line",
    "personal_data": "contains personal data ({value}); remove it",
}


def problems(results: list[GuardrailResult], draft: MemoDraft) -> list[dict[str, str]]:
    """The triggered findings in words the drafter can act on, with the claim they are about.
    For the fix prompt only: the evidence keeps positions and codes, not claim text."""
    out: list[dict[str, str]] = []
    for r in results:
        if r.outcome != "triggered":
            continue
        for issue in r.details.get("issues", []):
            section = draft.sections[issue["section"]]
            text = PROBLEM.get(
                issue["code"], "repeats an instruction from the documents; remove it"
            )
            out.append(
                {
                    "section": section.heading,
                    "claim": section.claims[issue["claim"]].text,
                    "problem": text.format(value=issue.get("value", "")),
                }
            )
    return out
