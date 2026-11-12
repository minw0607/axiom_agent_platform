"""A local screen for instructions hidden in borrower documents (prompt injection).

Borrower documents are data. Text in them that addresses the AI, tries to override its
rules, steers the credit decision, or hides itself from a human reader is a sign of an
attack. Any match quarantines the package: the job stops before any model sees it, and the
evidence log records which document, which line and which pattern, never the text itself.

This is a pattern screen, cheap and explainable but easy to evade with rewording. It runs
in every mode; the cloud screen (`injection_shield`, Prompt Shields) is meant to sit beside
it. Its patterns are measured against the adversarial set in week 5 and false positives
against the clean packages in every test run.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from axiom.guardrails.base import Stage, Subject, Verdict

# Characters a reader cannot see: zero-width, bidirectional controls, Unicode tag characters.
INVISIBLE = re.compile(
    "[\u200b-\u200f\u202a-\u202e\u2060-\u2064\u2066-\u2069\ufeff\U000e0000-\U000e007f]"
)

PATTERNS: dict[str, re.Pattern[str]] = {
    # "Ignore all previous instructions", "disregard your rules"
    "override_instructions": re.compile(
        r"\b(ignore|disregard|forget|override|bypass)\b[^.\n]{0,40}"
        r"\b(instructions?|rules?|guidelines?|prompts?|directions?|polic(y|ies))\b"
    ),
    # "You are now a ...", "system prompt", "developer message"
    "role_change": re.compile(
        r"\byou are now\b|\bfrom now on,? you\b|\bnew instructions?\b"
        r"|\b(system|developer) (prompt|message|instructions?)\b"
    ),
    # "Note to the AI", "assistant: you must ..."
    "addresses_the_ai": re.compile(
        r"\b(note|message|instructions?) (to|for) (the )?(ai|assistant|model|llm|reviewer)\b"
        r"|\b(ai|assistant|language model|llm|chatbot)s?\b[^.\n]{0,30}\b(must|should|shall|will)\b"
    ),
    # "Approve this loan", "rate this borrower as low risk"
    "steers_the_decision": re.compile(
        r"\b(approve|grant|accept)\b[^.\n]{0,20}\b(this|the) (loan|application|request|credit)\b"
        r"|\b(rate|mark|classify|treat|score)\b[^.\n]{0,30}"
        r"\bas (low[- ]risk|approved|creditworthy)\b"
        r"|\b(do not|don't|never) (mention|flag|report|include)\b"
    ),
    # Chat-template tokens and markup a renderer hides
    "hidden_markup": re.compile(
        r"<\|[a-z_]+\|>|\[/?(inst|system)\]|<!--|<\s*(script|style)\b", re.IGNORECASE
    ),
}


def normalize(text: str) -> str:
    """Fold look-alike characters and case so trivial disguises still match."""
    return unicodedata.normalize("NFKC", INVISIBLE.sub("", text)).casefold()


def scan(text: str) -> list[tuple[int, str]]:
    """(line number, pattern code) for every suspicious line, numbered from 1."""
    hits: list[tuple[int, str]] = []
    for n, line in enumerate(text.split("\n"), 1):
        if INVISIBLE.search(line):
            hits.append((n, "invisible_characters"))
        folded = normalize(line)
        hits += [(n, code) for code, pattern in PATTERNS.items() if pattern.search(folded)]
    return hits


@dataclass(frozen=True)
class InjectionScreen:
    name: str = "injection_screen"
    stage: Stage = "input"
    description: str = "Quarantines documents that carry instructions aimed at the AI"

    async def check(self, subject: Subject) -> Verdict:
        found = {d.doc_id: scan(d.text) for d in subject.documents}
        found = {doc_id: hits for doc_id, hits in found.items() if hits}
        if not found:
            return Verdict("passed", "no instructions aimed at the AI found")
        codes = sorted({code for hits in found.values() for _, code in hits})
        return Verdict(
            "triggered",
            f"quarantined {len(found)} document{'s' if len(found) != 1 else ''}: "
            + ", ".join(codes),
            details={
                "quarantined": {
                    doc_id: [{"line": n, "pattern": code} for n, code in hits]
                    for doc_id, hits in found.items()
                }
            },
            action="quarantined",
        )


INJECTION_SCREEN = InjectionScreen()
