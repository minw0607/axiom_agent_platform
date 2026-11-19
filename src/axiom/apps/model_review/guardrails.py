"""Model documentation review's part of the guardrail suite: its decision phrases.

The report may say what the documentation lacks and what the validator should look at. It
must never state the validator's decision: that the model is approved, validated or fit for
use (ADR 0016, rule 6). The check is the core's; these phrases are this app's.
"""

from __future__ import annotations

import re
from pathlib import Path

from axiom.guardrails import Guardrail, GuardrailSuite, default_controls, load_suite
from axiom.guardrails.output import decision_language

DECISION_PHRASES: dict[str, re.Pattern[str]] = {
    "model_stated_as_approved": re.compile(
        r"\b(model|system|it)\b[^.;]{0,30}"
        r"\b(is|has been|was|are|have been) (hereby |now |therefore )?"
        r"(approved|validated|certified|cleared|fit for (use|purpose)|safe to use)\b"
    ),
    "first_person_decision": re.compile(
        r"\bwe (hereby )?(approve|validate|certify|sign off on|clear)\b"
    ),
    "validation_decision": re.compile(
        r"\b(passes|passed|fails|failed) (model )?validation\b"
        r"|\bfinal (validation )?(decision|opinion)\b|\bvalidation (is )?complete\b"
    ),
}

# What the automated fix tells the drafter for each code.
DECISION_MESSAGES = {
    "model_stated_as_approved": "states that the model is approved or validated; only the "
    "validator decides, so describe what the documentation shows instead",
    "first_person_decision": "states a validation decision as made; only the validator decides",
    "validation_decision": "states a validation outcome; the report is a draft for the validator",
}

DECISION_LANGUAGE = decision_language(DECISION_PHRASES, "No validation decision stated as made")


def controls() -> list[Guardrail]:
    return default_controls(DECISION_LANGUAGE)


def suite(path: Path) -> GuardrailSuite:
    return load_suite(path, controls())
