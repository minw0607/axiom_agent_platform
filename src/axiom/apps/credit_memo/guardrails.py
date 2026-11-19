"""The credit memo's part of the guardrail suite: its decision phrases (ADR 0016).

The decision-language check is the core's; what counts as stating a credit decision as made
is the credit memo's. Every other control is the core's as it stands.
"""

from __future__ import annotations

import re
from pathlib import Path

from axiom.guardrails import Guardrail, GuardrailSuite, default_controls, load_suite
from axiom.guardrails.output import RAILS as CORE_RAILS
from axiom.guardrails.output import decision_language

DECISION_PHRASES: dict[str, re.Pattern[str]] = {
    "decision_stated_as_made": re.compile(
        r"\b(loan|credit|application|request|facility|borrower)\b[^.;]{0,30}"
        r"\b(is|has been|was|are|have been) (hereby |now )?"
        r"(approved|declined|denied|granted|rejected|funded)\b"
    ),
    "first_person_decision": re.compile(
        r"\bwe (hereby )?(approve|decline|deny|grant|reject|will (fund|lend))\b"
    ),
    "final_decision": re.compile(r"\bfinal (credit )?decision\b|\bdecision is final\b"),
}

# What the automated fix tells the drafter for each code.
DECISION_MESSAGES = {
    "decision_stated_as_made": "states a credit decision as made; only the credit officer "
    "decides, so describe the draft recommendation instead",
    "first_person_decision": "states a credit decision as made; only the credit officer decides",
    "final_decision": "calls something a final decision; the memo is a draft",
}

DECISION_LANGUAGE = decision_language(DECISION_PHRASES, "No credit decision stated as made")

# Every rail's patterns as the credit memo runs them (used by the alignment evaluation).
RAILS = {**CORE_RAILS, "decision_language": DECISION_PHRASES}


def controls() -> list[Guardrail]:
    """The platform's controls, with the credit memo's decision phrases."""
    return default_controls(DECISION_LANGUAGE)


def suite(path: Path) -> GuardrailSuite:
    """The credit memo's guardrails, switched on and off by config/guardrails.yaml."""
    return load_suite(path, controls())
