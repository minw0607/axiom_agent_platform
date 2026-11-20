"""The credit memo's part of the guardrail suite: its decision and steering phrases (ADR 0016).

The checks are the core's; what counts as stating a credit decision as made, or as a
borrower document steering it, is the credit memo's. Every other control is the core's as
it stands.
"""

from __future__ import annotations

import re
from pathlib import Path

from axiom.guardrails import Guardrail, GuardrailSuite, default_controls, load_suite
from axiom.guardrails.injection import injection_screen
from axiom.guardrails.output import RAILS as CORE_RAILS
from axiom.guardrails.output import decision_language, on_task, on_task_patterns

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

# A borrower document steering the decision: "approve this loan", "rate this borrower as low
# risk". The input screen adds these to the core's steers_the_decision.
APPROVAL_REQUEST = re.compile(
    r"\b(approve|grant|accept)\b[^.\n]{0,20}\b(this|the) (loan|application|request|credit)\b"
)
RATING_REQUEST = re.compile(
    r"\b(rate|mark|classify|treat|score)\b[^.\n]{0,30}\bas (low[- ]risk|approved|creditworthy)\b"
)
STEERING_PHRASES = (APPROVAL_REQUEST, RATING_REQUEST)
# The same, echoed in a draft. Not the approval request: a draft recommendation says
# "approve the loan request" legitimately (week 6 live baseline).
ECHOED_STEERING = (RATING_REQUEST,)

# What the automated fix tells the drafter for each code.
DECISION_MESSAGES = {
    "decision_stated_as_made": "states a credit decision as made; only the credit officer "
    "decides, so describe the draft recommendation instead",
    "first_person_decision": "states a credit decision as made; only the credit officer decides",
    "final_decision": "calls something a final decision; the memo is a draft",
}

DECISION_LANGUAGE = decision_language(DECISION_PHRASES, "No credit decision stated as made")
INJECTION_SCREEN = injection_screen(STEERING_PHRASES)
ON_TASK = on_task(ECHOED_STEERING)

# Every rail's patterns as the credit memo runs them (used by the alignment evaluation).
RAILS = {
    **CORE_RAILS,
    "on_task": on_task_patterns(ECHOED_STEERING),
    "decision_language": DECISION_PHRASES,
}


def controls() -> list[Guardrail]:
    """The platform's controls, with the credit memo's decision and steering phrases."""
    return default_controls(DECISION_LANGUAGE, INJECTION_SCREEN, ON_TASK)


def suite(path: Path) -> GuardrailSuite:
    """The credit memo's guardrails, switched on and off by config/guardrails.yaml."""
    return load_suite(path, controls())
