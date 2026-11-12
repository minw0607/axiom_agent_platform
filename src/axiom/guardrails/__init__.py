"""Layered guardrails (ADR 0004): one interface, switched on and off in config/guardrails.yaml."""

from __future__ import annotations

from pathlib import Path

import yaml

from axiom.guardrails.base import (
    STAGES,
    Guardrail,
    GuardrailConfigError,
    GuardrailResult,
    GuardrailSuite,
    Subject,
    Verdict,
    latest,
)
from axiom.guardrails.cloud import CONTENT_SAFETY, INJECTION_SHIELD

__all__ = [
    "STAGES",
    "Guardrail",
    "GuardrailConfigError",
    "GuardrailResult",
    "GuardrailSuite",
    "Subject",
    "Verdict",
    "default_controls",
    "latest",
    "load_suite",
]


def default_controls() -> list[Guardrail]:
    """The platform's controls in the order they run within each stage."""
    return [INJECTION_SHIELD, CONTENT_SAFETY]


def read_switches(path: Path) -> dict[str, bool]:
    raw = yaml.safe_load(path.read_text()) or {}
    controls = raw.get("controls")
    if not isinstance(controls, dict):
        raise GuardrailConfigError(f"{path.name} needs a 'controls' mapping")
    switches: dict[str, bool] = {}
    for name, entry in controls.items():
        enabled = entry.get("enabled") if isinstance(entry, dict) else None
        if not isinstance(enabled, bool):
            raise GuardrailConfigError(f"{name}: 'enabled' must be true or false")
        switches[str(name)] = enabled
    return switches


def load_suite(path: Path, controls: list[Guardrail] | None = None) -> GuardrailSuite:
    return GuardrailSuite(controls or default_controls(), read_switches(path))
